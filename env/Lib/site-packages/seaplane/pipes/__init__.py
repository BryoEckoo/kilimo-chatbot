import asyncio
from collections import OrderedDict
from importlib.metadata import version
import json
import math
import os
import time
from typing import Any, Callable, Dict, List, Optional, Protocol, Set, Tuple, Union

from seaplane.config import config
from seaplane.errors import SeaplaneError
from seaplane.logs import log
from seaplane.modelhub import Modelhub
from seaplane.sdk_internal_utils.buckets import create_bucket_if_needed
from seaplane.substation import Substation

"""
Deploy your applications to the Seaplane platform by using
Apps, Dags, and Flows.

# An App is a unit of deployment on Seaplane.
# Use an app to connect units of work together,
# and associate your application with Seaplane
# resources like HTTP endpoints and object store buckets.

app = seaplane.pipes.App("my-app")

# The work of apps is composed of Dags. Dags (Directed Acyclic Graphs)
# are collections of connections between the working python
# functions in your application. You can create an Dag using
# an application.

dag = app.dag("my-dag")

# You can use a dag to build Flows, which map to running
# processes on Seaplane infrastructure. To create a
# Flow, you need a dag, and some input sources, and a python
# function that does the work of the Flow.

def emphasize(context):
    yield context.body + b'!'

def intensify(context):
    yield context.body + 'b' for real'

# The first argument to dag.task is the function, the second is the input.

intensify_task = dag.task(intensify, [app.input()])
emphasize_task = dag.task(emphasize, [intensify_task])

# to associate an output with a dag, call `dag.respond()`

dag.respond(emphasize_task)

# to associate the output of a Flow or Dag with an App, do the same thing

app.respond(dag) # you can also write `app.respond(emphasize_task)` here

# Calling app.start() will deploy an application from your workstation,
# and *run* that application on the Seaplane platform.
app.start()
"""


class ExecutionResult(Protocol):
    """
    This is just to prevent a circular import of seaplane.pipes.executor.Result
    """

    address_label: Optional[str]


class Subject:
    """
    Subject is a pubsub subject. Flows publish messages associated with
    a subject to communicate with the outside world.

    Subject should represent a concrete, publishable subject - it can
    contain fun ${! meta} macros, but should not contain wildcards.

    Flows can write to subjects but not subscribe to them.

    app.edge(my_task, Subject("known-stream.explicit-subject"))
    """

    def __init__(self, subject_string: str):
        self.subject_string = subject_string


class Subscription:
    """
    The possibly-filtered name of a subject, for matching. Flows listen
    to subscriptions for their inputs.

    Should not ever contain macros and things, but may contain wildcards.

    Tasks can subscribe to Subscriptions but not write to them.

    app.edge(Subscription("known-stream.explicit-subscription.>"), my_task)
    """

    def __init__(self, filter: str, deliver: str = "all"):
        self.filter = filter
        self.deliver = deliver

    def stream(self) -> str:
        return self.filter.split(".", 1)[0]

    def matches_subject(self, subject_string: str) -> bool:
        """
        Returns true only if the given subject string is a carrier subject
        that would match this subscription.

        A single subscription can match multiple carrier subjects,
        and it can be useful to identify the specific subject that
        was matched; for example, you can identify the source of
        a message in one of your work functions like this:

        def which_subscriptions(message):
            # "nats_subject" contains the Subject string
            # associated with this message.
            message_subject = message.meta["nats_subject"]

            # message.flow.subscriptions is a set of all
            # of the subscriptions (and thus, all of the
            # possible sources) of the given message.
            possibles = message.flow.subscriptions

            # You can use matches_subject to find the
            # subscriptions that are relevant
            possible_sources = {
                s for s in possibles
                if s.matches_subject(message_subject)
            }
        """
        # This match is approximate - in the medium term,
        # it would be nice to get this information
        # from Carrier itself when we get a message.
        #
        # See (and maybe advocate for) https://linear.app/seaplane/issue/CAR-343
        # if this becomes a problem
        subject_segments = subject_string.split(".")
        filter_segments = self.filter.split(".")

        while subject_segments and filter_segments:
            next_subject = subject_segments.pop(0)
            next_filter = filter_segments.pop(0)

            if next_filter == ">":
                return True

            if next_filter != "*" and next_filter != next_subject:
                return False

        # if both lists were consumed, we match
        return len(subject_segments) == 0 and len(filter_segments) == 0


# Our platform supports a total of 16 dot-separated segments
# of a given subject. We already use a number
# of sub-parts for the stream and flow name,
# the request id associated with a message,
# and quite a few segments for batching.
# We burn 4 segments here.
#
# Users who hit the predicate limit can try
# combining their predicates into single functions,
# or using the address_label / listen_to_address_label mechanismx
MAX_ADDRESS_PREDICATES = 4


class FlowTopic:
    """
    Flow subjects conform to a standard format

        app.task.label.address-tag.output-id.batch.batch.batch

    this means a FlowTopic can be used both create a subscription
    to listen to a flow, and to create a subject to publish from a Flow.

    The address-tag of a FlowTopic has an internal standard format

        address-label.Predicate1.Predicate2.Predicate3.Predicate4

    The address label is an arbitrary string that can be set in the
    flow work function, and can be used by downstream flows to filter
    their inputs.

    The predicates are associated with an optional boolean function
    of the results of the FlowTopic's flow, that will be set to "T" if
    that function returns a true value for a given result, to "F" if
    that function returns false for a given result, and "x" if the function
    isn't defined.

    You can route messages using predicates with the `if_`, `else_`, and
    `elif_` methods.

    """

    @classmethod
    def root(cls, dag_name: str, instance_name: str) -> "FlowTopic":
        """
        Produces the root of a (potential) tree of FlowTopics, that
        can be constrained with if_, else_ and listen_to_address_label
        """
        predicates: List[Optional[Callable[..., Any]]] = [None] * MAX_ADDRESS_PREDICATES
        matches: List[Optional[bool]] = [None] * MAX_ADDRESS_PREDICATES
        return FlowTopic(dag_name, instance_name, None, -1, predicates, matches)

    def __init__(
        self,
        dag_name: str,
        instance_name: str,
        address_label: Optional[str],
        predicate_index: int,
        predicates: List[Optional[Callable[..., Any]]],
        matches: List[Optional[bool]],
    ):
        self.dag_name = dag_name
        self.instance_name = instance_name
        self.address_label = address_label

        # predicates is shared between FlowTopics, but matches is not
        self.predicate_index = predicate_index
        self.predicates = predicates
        self.matches = matches

    def if_(self, new_predicate: Callable[..., Any]) -> "FlowTopic":
        """
        Returns a new topic, narrowed to only those messages from
        the source topic that also match the given predicate. The
        returned topic will be associated with fewer messages in
        general.

        if_ is supported on topics, but also on Flows and Dags
        (which pass the calls along to their underlying topics.)

        For example:

        all_work = dag.task(work, ...)

        # The task associated with f will only get messages from
        # work if the length of the result body is greater than 10
        dag.task(f, [all_work.if_(lambda r: len(r.body) > 10)])

        # You can still use the original task to connect with
        # another one that gets *All* of the messages
        dag.task(g, [all_work])

        # And you can make more than one constraint to apply
        # in different places
        dag.task(h, [all_work.if_(lambda r: len(r.body) > 100)])

        Flows can only be associated with a limited number of
        predicates. If you get "too many conditions" errors,
        consider using an address_label and listen_to_address_label
        for a more robust addressing mechanism.
        """
        try:
            new_ix = self.predicates.index(None)
        except ValueError:
            raise SeaplaneError(
                "too many conditions for conditional dispatch."
                " use .listen_to_address_label() instead, for more powerful conditional dispatch"
                " in your app"
            )

        self.predicates[new_ix] = new_predicate
        new_matches = self.matches.copy()
        new_matches[new_ix] = True

        return FlowTopic(
            self.dag_name,
            self.instance_name,
            self.address_label,
            new_ix,
            self.predicates,
            new_matches,
        )

    def else_(self) -> "FlowTopic":
        """
        Match the inverse of the predicate associated with this particular topic.
        Using else_ only makes sense for topics that have already been
        constrained with if_

        starts_with_x = work.if_(lambda r: r.body.startswith(b"x"))

        doesnt_start_with_x = starts_with_x.else_()

        Using if_ and else_ together can cover all messages produced by a task
        """
        if self.predicate_index == -1:
            raise SeaplaneError(
                "can't call _else on something that doesn't have a predicate."
                " Use `else_` when you've already used `if_` to do conditional dispatch"
                " (or use a label directly for more power and ease of use.)"
            )

        new_matches = self.matches.copy()
        new_matches[self.predicate_index] = not new_matches[self.predicate_index]

        return FlowTopic(
            self.dag_name,
            self.instance_name,
            self.address_label,
            self.predicate_index,
            self.predicates,
            new_matches,
        )

    def elif_(self, new_predicate: Callable[..., Any]) -> "FlowTopic":
        """
        a convenience for topic.else_().if_(). As with else_, this only
        makes sense to use on topics that have already been constrained
        with if_(), and as with if_(), the underlying flow for a topic
        can only support a limited number of predicates.
        """
        return self.else_().if_(new_predicate)

    def listen_to_address_label(self, address_label: str) -> "FlowTopic":
        """
        Constrain a topic to only those messages that have had a given
        address_label set on them. You can use it like this:

        def decide(message):
            if is_good(message.body):
                result = message.result(b"it was good!")
                message.address_label = "good"
            else:
                result = message.result(b"it was maybe not so good.")
                message.address_label = "bad"

            return result

        decision = dag.task(decide, ...)
        only_good_ones = decision.listen_to_address_label("good")
        only_bad_ones = decision.listen_to_address_label("bad")

        dag.task(process_goods, [only_good_ones])
        dag.task(process_bads, [only_bad_ones])
        dag.task(process_everything, [decision])

        address_labels are useful in cases where there are
        many different alternatives to choose from.
        """
        return FlowTopic(
            self.dag_name,
            self.instance_name,
            address_label,
            self.predicate_index,
            self.predicates,
            self.matches.copy(),
        )

    @property
    def subject(self) -> Subject:
        return Subject(
            f"{self.dag_name}.{self.instance_name}"
            '.${! meta("_seaplane_address_tag") }'
            '.${! meta("_seaplane_output_id") }'
            '${! meta("_seaplane_batch_hierarchy") }'  # Note no leading "."
        )

    @property
    def subscription(self) -> Subscription:
        subfilters = ["*" if self.address_label is None else self.address_label]
        for m in self.matches:
            if m is None:
                subfilters.append("*")
            elif m:
                subfilters.append("T")
            else:
                subfilters.append("F")

        address_filter = ".".join(subfilters)
        filter = f"{self.dag_name}.{self.instance_name}.{address_filter}.>"
        return Subscription(filter)

    def address_tag(self, r: ExecutionResult) -> str:
        # This produces a concrete address label for a subject, for use
        # at publish time.
        subtags = ["X" if r.address_label is None else r.address_label]
        for pred in self.predicates:
            if pred is None:
                subtags.append("x")
            elif pred(r):
                subtags.append("T")
            else:
                subtags.append("F")
        return ".".join(subtags)


class Flow:
    """
    Description of deployment intent associated with a Seaplane flow.

    Create flows through an Dag, like this

    app = App("app")

    dag = app.dag("task-demo")

    def do_it(context: Messsage) -> Result:
        yield Result(b"Hello, " + context.body)

    my_task = dag.flow(do_it)

    Flow functions will be passed a Seaplane message, and
    should yield a Seaplane result. For convenience,
    if a task returns bytes (or something that can be converted
    into bytes), that value will be converted to bytes
    and packaged into a Result automatically.

    Once created, Flows can be wired together with other flows,
    application resources, or other Dags with `dag.edge`

    dag.edge(app.input(), my_task)
    dag.edge(my_task, app.output())
    """

    def __init__(
        self,
        dag: "Dag",
        work: Callable[..., Any],
        instance_name: str,
        topic: FlowTopic,
        replicas: int,
        ack_wait_secs: int,
        cpu: int,
        mem: int,
    ):
        # Note that dag is a circular reference, so Flows, Dags, and Apps will not be GC'd
        self.dag = dag
        self.work = work
        self.instance_name = instance_name

        self.replicas = replicas
        self.ack_wait_secs = ack_wait_secs
        self.cpu = cpu
        self.mem = mem
        self.subscriptions: Set[Subscription] = set()  # pull input from these subjects

        # We allow callers to configure writes to an explicit
        # (and therefore opaque to us) carrier Subject, *or*
        # a standard (and manipulable) FlowTopic.
        # Removing or replacing if_-style conditionals with
        # something that is a better fit to the platform
        # is probably the fix for this if it turns out to be a hassle.
        self.write: Union[FlowTopic, Subject] = topic  # task output

    def subscribe(self, source: Subscription) -> None:
        self.subscriptions.add(source)

    def set_static_subject(self, subject: Subject) -> None:
        # Overrides (and destroys) the default FlowTopic
        # and instead publishes exactly to the given subjext.
        # Internally, we use this function to attach
        # flows to output endpoints.
        #
        # Flows with static_subjects are much less flexible
        # than flows that publish to FlowTopics - once
        # set_static_subject has been called, users
        # are responsible for constructing their own
        # subscriptions, and can't use tooling like
        # conditional dispatch to listen to a Flow's output.
        if isinstance(self.write, Subject):
            raise SeaplaneError(
                f"{self.instance_name} is already configured to write to"
                f" {self.write.subject_string}. To write to two places, use another flow"
            )

        log.logger.debug(
            f"configuring flow {self.instance_name} with static subject {subject.subject_string}"
        )

        self.write = subject

    def if_(self, predicate: Callable[..., Any]) -> FlowTopic:
        """
        Returns a Seaplane carrier topic associated with the output of
        this Flow and constrained by the given predicate. See FlowTopic.if_
        for details and examples.
        """
        if not isinstance(self.write, FlowTopic):
            raise SeaplaneError(
                f"flow {self.instance_name} is configured to write to"
                f" {self.subject.subject_string}, which doesn't support"
                " conditional reading."
            )
        return self.write.if_(predicate)

    def listen_to_address_label(self, label: str) -> FlowTopic:
        """
        Returns a Seaplane carrier topic associated with this Flow and
        constrained to only those outputs that match the given address label.
        See FlowTopic.listen_to_address_label for details and exampls.
        """
        if not isinstance(self.write, FlowTopic):
            raise SeaplaneError(
                f"flow {self.instance_name} is configured to write to"
                f" {self.subject.subject_string}, which doesn't support"
                " address labeling. Instead, use an explicit Subscription."
            )
        return self.write.listen_to_address_label(label)

    @property
    def subject(self) -> Subject:
        if isinstance(self.write, FlowTopic):
            return self.write.subject
        else:
            return self.write

    def calculate_address(self, r: ExecutionResult) -> Optional[str]:
        if isinstance(self.write, FlowTopic):
            return self.write.address_tag(r)

        return None


class Bucket:
    """
    A reference to a bucket and it's associated notification subscription.

    Get a bucket by asking the app to query the Seaplane infrastructure, like this:

    app = App("bucket-demo-app")
    dag = app.dag("dag")

    # This queries the Seaplane infrastructure, and may creaet a bucket
    # if one doesn't exist already
    bucket = app.bucket("bucket-demo-bucket")

    # once you have a bucket in hand, it can be used as the source
    # of events for a flow
    dag.task(observe_bucket, [bucket])
    """

    def __init__(self, name: str, notify: str):
        self.name = name
        self.notify_subscription = Subscription(notify)


# For historical reasons, we need to be sure that
# these defaults work the same both for Dag.flow() and Dag.task()
_DEFAULT_REPLICAS = 1
_DEFAULT_ACK_WAIT_SECS = 2 * 60
_DEFAULT_CPU = 100
_DEFAULT_MEM = 512


class SubstationRequest(Flow):
    """
    SubstationRequest represents request to Seaplane Substation Proxy.

    Unlike most subscriptions, tasks subscribing to the seaplane substation endpoint
    are configured to receive only new messages, rather than pull all messages
    from their upstream peers in order.
    """

    def __init__(
        self,
        dag: "Dag",
        make_request: Callable[..., Any],
        instance_name: str,
        topic: FlowTopic,
        substation_instance_name: str,
    ):
        self.substation_instance_name = substation_instance_name
        super(SubstationRequest, self).__init__(
            dag,
            make_request,
            instance_name,
            topic,
            _DEFAULT_REPLICAS,
            _DEFAULT_ACK_WAIT_SECS,
            _DEFAULT_CPU,
            _DEFAULT_MEM,
        )


class SubstationResponse(Flow):
    def __init__(
        self,
        dag: "Dag",
        get_response: Callable[..., Any],
        instance_name: str,
        topic: FlowTopic,
        substation_instance_name: str,
    ):
        self.substation_instance_name = substation_instance_name
        super(SubstationResponse, self).__init__(
            dag,
            get_response,
            instance_name,
            topic,
            _DEFAULT_REPLICAS,
            _DEFAULT_ACK_WAIT_SECS,
            _DEFAULT_CPU,
            _DEFAULT_MEM,
        )


class SubstationSubscription(Subscription):
    """
    SubstationSubscription represents response of Seaplane Substation Proxy.

    Unlike most subscriptions, tasks subscribing to the seaplane substation endpoint
    are configured to receive only new messages, rather than pull all messages
    from their upstream peers in order.
    """

    def __init__(self, dag_name: str, substation_instance_name: str, response_sub: str):
        self.dag_name = dag_name
        self.instance_name = f"{dag_name}-sub-subscription"
        self.substation_instance_name = substation_instance_name
        super(SubstationSubscription, self).__init__(response_sub, "new")


class OutEndpoint(Subject):
    """
    The output seaplane endpoint. Does not accept subscriptions.

    Get this by calling App.output()
    """

    def __init__(self, endpoint: str):
        super(OutEndpoint, self).__init__(
            f"_SEAPLANE_ENDPOINT.out.{endpoint}."
            '${! meta("_seaplane_output_id") }'
            '${! meta("_seaplane_batch_hierarchy") }'
        )


class InEndpoint(Subscription):
    """
    Endpoint represents the Subject of a seaplane input endpoint.

    Unlike most subscriptions, tasks subscribing to the seaplane input endpoint are
    configured to receive only new messages, rather than pull all messages
    from their upstream peers in order.

    Get one of these with App.input()
    """

    def __init__(self, endpoint: str):
        super(InEndpoint, self).__init__(f"_SEAPLANE_ENDPOINT.in.{endpoint}.>", deliver="new")
        self.endpoint = endpoint


EdgeFrom = Union[
    Flow,
    FlowTopic,
    "Dag",
    "Bucket",
    SubstationRequest,
    SubstationResponse,
    SubstationSubscription,
    InEndpoint,
    Subscription,
]
EdgeTo = Union[Flow, OutEndpoint, SubstationRequest, SubstationResponse, Subject]


class Dag:
    """
    Dag is a namespace and unit of deployment for
    a collection of Tasks. Dags create a namespace for task messages
    and manage deployment together.

    Create Dags through an application, like this

    app = App("my-app")
    dag = app.dag("my-dag")

    Dags are a namespace for their associated flows, so while the names
    of flows must be unique per dag, you can use the same flow name
    in different Dags. You can communicate between Dags either by
    wiring their flows together directly, or by calling `dag.respond()`
    with a flow that represents the single output of the dag (if that
    makes sense for your application)

    dag1 = app.dag("dag-1")
    dag2 = app.dag("dag-2")

    t1 = dag1.task(work1, [app.input()])
    dag1.respond(t1)

    # It's ok to connect tasks across Dag boundaries
    t2 = dag2.task(work2, [t1])

    # since we've configured dag1 to respond with t1's output, this works just the same
    # as the line above
    t2 = dag2.task(work2, [dag1])
    """

    def __init__(self, name: str):
        """use App.dag to create Dags"""
        self.name = name
        self.flow_registry: OrderedDict[str, Flow] = OrderedDict()
        self.response: Optional[Flow] = None
        self.edges: List[OrderedDict[str, str]] = []

    def substation_request(self, substation: Substation, input_list: List[EdgeFrom]) -> Flow:
        def make_request(msg: Any) -> Any:
            input_data = json.loads(msg.body)
            request_data = substation.make_request(input_data)
            ret = msg.result(json.dumps(request_data).encode())
            yield ret

        instance_name = self.name + "-sub-req"

        if instance_name in self.flow_registry:
            raise SeaplaneError(
                f"duplicate task name {instance_name} in dag {self.name}."
                " Try providing a unique instance_name argument when you create your task"
            )

        topic = FlowTopic.root(self.name, instance_name)
        ret = SubstationRequest(
            self,
            make_request,
            instance_name,
            topic,
            substation_instance_name=substation.app_division,
        )
        self.flow_registry[instance_name] = ret
        for source in input_list:
            self.edge(source, ret)
        return ret

    def modelhub_request(self, modelhub: Modelhub, input_list: List[EdgeFrom]) -> Flow:
        def make_request(msg: Any) -> Any:
            input_data = json.loads(msg.body)
            request_data = modelhub.make_request(input_data)
            ret = msg.result(json.dumps(request_data).encode())
            yield ret

        substation = modelhub.substation
        instance_name = f"{self.name}-mdh-req"

        if instance_name in self.flow_registry:
            raise SeaplaneError(
                f"duplicate task name {instance_name} in dag {self.name}."
                " Try providing a unique instance_name argument when you create your task"
            )

        topic = FlowTopic.root(self.name, instance_name)
        ret = SubstationRequest(
            self,
            make_request,
            instance_name,
            topic,
            substation_instance_name=substation.app_division,
        )
        self.flow_registry[instance_name] = ret
        for source in input_list:
            self.edge(source, ret)
        return ret

    def substation_response(self, substation: Substation, input_list: List[EdgeFrom]) -> Flow:
        instance_name = self.name + "-sub-resp"

        if instance_name in self.flow_registry:
            raise SeaplaneError(
                f"duplicate task name {instance_name} in dag {self.name}."
                " Try providing a unique instance_name argument when you create your task"
            )

        topic = FlowTopic.root(self.name, instance_name)
        ret = SubstationResponse(
            self,
            substation.get_response,
            instance_name,
            topic,
            substation_instance_name=substation.app_division,
        )
        self.flow_registry[instance_name] = ret
        for source in input_list:
            self.edge(source, ret)
        return ret

    def modelhub_response(self, modelhub: Modelhub, input_list: List[EdgeFrom]) -> Flow:
        substation = modelhub.substation
        instance_name = self.name + "-mdh-resp"

        if instance_name in self.flow_registry:
            raise SeaplaneError(
                f"duplicate task name {instance_name} in dag {self.name}."
                " Try providing a unique instance_name argument when you create your task"
            )

        topic = FlowTopic.root(self.name, instance_name)
        ret = SubstationResponse(
            self,
            substation.get_response,
            instance_name,
            topic,
            substation_instance_name=substation.app_division,
        )
        self.flow_registry[instance_name] = ret
        for source in input_list:
            self.edge(source, ret)
        return ret

    def substation_subscription(
        self, app_name: str, dag_name: str, substation: Substation
    ) -> Subscription:
        response_sub = f"{substation.results_stream()}.{app_name}-{dag_name}.>"
        return SubstationSubscription(self.name, substation.app_division, response_sub)

    def modelhub_subscription(
        self, app_name: str, dag_name: str, modelhub: Modelhub
    ) -> Subscription:
        substation = modelhub.substation
        ret = self.substation_subscription(app_name, dag_name, substation)
        return ret

    def task(
        self,
        work: Callable[..., Any],
        sources: List[EdgeFrom],
        instance_name: Optional[str] = None,
        **kwargs: int,
    ) -> Flow:
        """
        Dag.task() is a convenience method for describing a new flow, along
        with its inputs.

        Dag.task() returns a reference to the created flow, that can be used
        to subscribe subsequent flows to the created flow's output.
        """
        replicas = kwargs.get("replicas", _DEFAULT_REPLICAS)
        ack_wait_secs = kwargs.get("ack_wait_secs", _DEFAULT_ACK_WAIT_SECS)
        cpu = kwargs.get("cpu", _DEFAULT_CPU)
        mem = kwargs.get("mem", _DEFAULT_MEM)
        ret = self.flow(
            work,
            instance_name=instance_name,
            replicas=replicas,
            ack_wait_secs=ack_wait_secs,
            cpu=cpu,
            mem=mem,
        )
        for source in sources:
            self.edge(source, ret)

        return ret

    def respond(self, tail: Union["Dag", Flow]) -> None:
        """
        Dag.respond() is a convenience for wiring tasks to
        the output of the dag.

        While you can wire flows together directly across dag boundaries,
        for many dags (that have a single, interesting output) it can
        be convenient to treat the dag itself as the source of messages.
        To do this, call `Dag.respond()` with the interesting output
        flow as an argument.

        Dags that have a response configured can be used as sources for
        other Flows.
        """
        if isinstance(tail, Dag):
            if tail.response is None:
                raise SeaplaneError(
                    f"{tail.name} has no response. Call Dag.respond on it, "
                    "or connect directly to it's tasks"
                )
            self.respond(tail.response)
        else:
            self.response = tail

    def flow(
        self,
        work: Callable[..., Any],
        instance_name: Optional[str] = None,
        replicas: int = _DEFAULT_REPLICAS,
        ack_wait_secs: int = _DEFAULT_ACK_WAIT_SECS,
        cpu: int = _DEFAULT_CPU,
        mem: int = _DEFAULT_MEM,
    ) -> Flow:
        if instance_name is None:
            instance_name = work.__name__.replace("_", "-") + "-default"

        prefixed_name = self.name + "-" + instance_name

        if prefixed_name in self.flow_registry:
            raise SeaplaneError(
                f"duplicate task name {prefixed_name} in dag {self.name}."
                " Try providing a unique instance_name argument when you create your task"
            )

        topic = FlowTopic.root(self.name, prefixed_name)
        ret = Flow(self, work, prefixed_name, topic, replicas, ack_wait_secs, cpu, mem)
        self.flow_registry[ret.instance_name] = ret
        return ret

    def add_edge_representation(self, source: EdgeFrom, dest: EdgeTo) -> None:
        while isinstance(source, Dag):
            if source.response is None:
                raise SeaplaneError("Call Dag.response(..), or compose individual tasks together.")
            source = source.response

        source_node_id = ""
        target_node_id = ""

        if isinstance(source, Bucket):
            source_node_id = Representation.get_bucket_node_id(source.name)
        elif isinstance(source, SubstationRequest):
            source_node_id = Representation.get_substation_request_node_id(
                source.dag.name, source.instance_name, source.substation_instance_name
            )
        elif isinstance(source, SubstationResponse):
            source_node_id = Representation.get_substation_response_node_id(
                source.dag.name, source.instance_name, source.substation_instance_name
            )
        elif isinstance(source, SubstationSubscription):
            source_node_id = Representation.get_substation_subscription_node_id(
                source.dag_name, source.instance_name, source.substation_instance_name
            )
        elif isinstance(source, InEndpoint):
            source_node_id = Representation.IN_ENDPOINT_ID
        elif isinstance(source, Flow):
            source_node_id = Representation.get_flow_node_id(source.dag.name, source.instance_name)
        elif isinstance(source, FlowTopic):
            source_node_id = Representation.get_flow_node_id(source.dag_name, source.instance_name)
        elif isinstance(source, Subscription):
            log.logger.debug(
                "setting a subscription as input is an anti-pattern"
                " that will not be allowed in future SDK versions"
            )
        else:
            raise SeaplaneError(f"cannot represent an edge from source of type {type(source)}")

        if isinstance(dest, SubstationResponse):
            target_node_id = Representation.get_substation_response_node_id(
                dest.dag.name, dest.instance_name, dest.substation_instance_name
            )
        elif isinstance(dest, SubstationRequest):
            target_node_id = Representation.get_substation_request_node_id(
                dest.dag.name, dest.instance_name, dest.substation_instance_name
            )
        elif isinstance(dest, OutEndpoint):
            target_node_id = Representation.OUT_ENDPOINT_ID
        elif isinstance(dest, Flow):
            target_node_id = Representation.get_flow_node_id(dest.dag.name, dest.instance_name)
        elif isinstance(dest, Subject):
            log.logger.debug(
                "setting a subject as output is an anti-pattern"
                " that will not be allowed in future SDK versions"
            )
        else:
            raise SeaplaneError(f"cannot represent an edge to destination of type {type(dest)}")

        if source_node_id != "" and target_node_id != "":
            self.edges.append(Representation.get_edge(source_node_id, target_node_id))

    def edge(
        self,
        source: EdgeFrom,
        dest: EdgeTo,
    ) -> None:
        # Add an edge from a to b, binding b to a's output.
        # In general, edges must be between Flows and something
        # like a Subscription or a Subject, or that can
        # be reduced to a Subscription or Subject.
        #
        # Calling `edge` directly has a lot of sharp edges.
        # If possible, prefer constructing relationships
        # with Dag.task()

        # Most of the code here is trickery to cover the
        # various cases of "something like a Subscription"

        # Type normalization for source: Subscription

        self.add_edge_representation(source, dest)

        while isinstance(source, Dag):
            if source.response is None:
                raise SeaplaneError("Call Dag.response(..), or compose individual tasks together.")
            source = source.response

        if isinstance(source, Bucket):
            source = source.notify_subscription

        # Special case, wire a Flow to an outbound Subject
        if isinstance(source, Flow) and isinstance(dest, Subject):
            source.set_static_subject(dest)
            return

        if isinstance(source, Flow):
            if isinstance(source.write, Subject):
                raise SeaplaneError(
                    f"{source.instance_name} writes to explicit subject {source.write}"
                    " to make an edge between this and another flow, use an explicit filter"
                    " and make the edge in two parts."
                )
            source = source.write

        # break for type checking
        narrowed: Union[FlowTopic, Subscription] = source
        source = narrowed

        if not isinstance(dest, Flow):
            raise SeaplaneError(
                f"can't route messages from {source} to {dest}."
                " One of the ends of an edge must be a Flow."
            )

        if isinstance(source, FlowTopic):
            source = source.subscription

        # more type checking
        pair: Tuple[Subscription, Flow] = (source, dest)
        (source, dest) = pair

        if isinstance(source, Subscription):
            dest.subscribe(source)
        else:
            # Clients might not be using a type checker.
            raise RuntimeError(
                f"cannot wire an edge from {source} to {dest}."
                " Edges must go from Flows to Subjects, or from Subscriptions to Flows"
            )

    def if_(self, predicate: Callable[..., Any]) -> FlowTopic:
        if not isinstance(self.response, Flow):
            raise SeaplaneError(
                f"Call Dag.respond(flow) on {self.name} before using conditional dispatch."
            )
        return self.response.if_(predicate)

    def listen_to_address_label(self, label: str) -> FlowTopic:
        if not isinstance(self.response, Flow):
            raise SeaplaneError(
                f"Call Dag.respond(flow) on {self.name} before using address label filtering."
            )
        return self.response.listen_to_address_label(label)


class Representation:
    IN_ENDPOINT_ID = "in"
    OUT_ENDPOINT_ID = "out"

    @staticmethod
    def get_bucket_node_id(bucket_name: str) -> str:
        return bucket_name

    @staticmethod
    def get_flow_node_id(dag_name: str, flow_name: str) -> str:
        return f"{dag_name}.{flow_name}"

    @staticmethod
    def get_substation_request_node_id(
        dag_name: str, instance_name: str, substation_instance_name: str
    ) -> str:
        return f"{dag_name}.{instance_name}.{substation_instance_name}"

    @staticmethod
    def get_substation_response_node_id(
        dag_name: str, instance_name: str, substation_instance_name: str
    ) -> str:
        return f"{dag_name}.{instance_name}.{substation_instance_name}"

    @staticmethod
    def get_substation_subscription_node_id(
        dag_name: str, instance_name: str, substation_instance_name: str
    ) -> str:
        return f"{dag_name}.{instance_name}.{substation_instance_name}"

    @staticmethod
    def get_edge(source_id: str, target_id: str) -> OrderedDict[str, str]:
        edge_id = f"from.{source_id}.to.{target_id}"
        return OrderedDict[str, str]({"id": edge_id, "source": source_id, "target": target_id})

    @staticmethod
    def get_flow_node(dag_name: str, flow_name: str) -> OrderedDict[str, str]:
        flow_id = Representation.get_flow_node_id(dag_name, flow_name)
        return OrderedDict[str, str]({"id": flow_id, "name": flow_name})

    @staticmethod
    def get_bucket_node(bucket_name: str) -> OrderedDict[str, str]:
        bucket_id = Representation.get_bucket_node_id(bucket_name)
        return OrderedDict[str, str]({"id": bucket_id, "name": bucket_name})

    @staticmethod
    def get_substation_request_node(
        dag_name: str, instance_name: str, substation_instance_name: str
    ) -> OrderedDict[str, str]:
        substation_request_id = Representation.get_substation_request_node_id(
            dag_name, instance_name, substation_instance_name
        )
        return OrderedDict[str, str](
            {
                "id": substation_request_id,
                "name": instance_name,
                "substation_id": substation_instance_name,
            }
        )

    @staticmethod
    def get_substation_response_node(
        dag_name: str, instance_name: str, substation_instance_name: str
    ) -> OrderedDict[str, str]:
        substation_response_id = Representation.get_substation_response_node_id(
            dag_name, instance_name, substation_instance_name
        )
        return OrderedDict[str, str](
            {
                "id": substation_response_id,
                "name": instance_name,
                "substation_id": substation_instance_name,
            }
        )

    @staticmethod
    def get_substation_subscription_node(
        dag_name: str, instance_name: str, substation_instance_name: str
    ) -> OrderedDict[str, str]:
        substation_subscription_id = Representation.get_substation_subscription_node_id(
            dag_name, instance_name, substation_instance_name
        )
        return OrderedDict[str, str](
            {
                "id": substation_subscription_id,
                "name": instance_name,
                "substation_id": substation_instance_name,
            }
        )


class App:
    """
    An App is a unit of deployment to Seaplane. Use an App to create
    and deploy Dags, which in turn you will use to create and
    organize individual tasks.

    An App is associated with a set of endpoints you can use to listen
    to HTTP requests and push data out of your Seaplane application, and
    optional references to Seaplane object store buckets.
    """

    # TODO: change the singleton to a REGISTRY approach, there is no real
    # TODO: need to enforce single-app processes.
    _instance: Optional["App"] = None

    def __init__(self, name: str, name_prefix: Optional[str] = None):
        if App._instance is not None:
            # type system can't know stuff about App while we're defining it.
            iname = App._instance.name  # type: ignore
            raise SeaplaneError(
                f"you should only define a single app in your program. {iname} is already defined"
            )

        if name_prefix is None:
            name_prefix = config.name_prefix

        self.name_prefix = name_prefix
        self.name = name_prefix + name
        self.dag_registry: OrderedDict[str, Dag] = OrderedDict()
        self.buckets: Set[Bucket] = set()
        self.input_endpoint = InEndpoint(self.name)
        self.output_endpoint = OutEndpoint(self.name)
        App._instance = self

    @classmethod
    def instance(cls) -> Optional["App"]:
        return cls._instance

    def represention(self) -> Dict[str, Any]:
        """
        Return a JSON representation of the App
        """

        representation = OrderedDict[str, Any](
            {
                "graph": OrderedDict[str, Any](
                    {
                        "edges": [],
                        "nodes": [
                            OrderedDict[str, Any]({"id": "in", "name": "in"}),
                            OrderedDict[str, Any]({"id": "out", "name": "out"}),
                        ],
                    }
                ),
                "metadata": OrderedDict[str, Any](
                    {
                        "sdk_version": version("seaplane"),
                        "create_time": str(math.floor(time.time() * 1000)),  # ms
                    }
                ),
            }
        )

        # Add all bucket nodes
        for bucket_name in sorted([bucket.name for bucket in self.buckets]):
            representation["graph"]["nodes"].append(Representation.get_bucket_node(bucket_name))

        for dag_key in sorted(self.dag_registry.keys()):
            dag = self.dag_registry[dag_key]

            # Add all flow/substation nodes
            for flow_key in sorted(dag.flow_registry.keys()):
                flow = dag.flow_registry[flow_key]
                if isinstance(flow, SubstationRequest):
                    representation["graph"]["nodes"].append(
                        Representation.get_substation_request_node(
                            dag.name, flow.instance_name, flow.substation_instance_name
                        )
                    )
                elif isinstance(flow, SubstationResponse):
                    representation["graph"]["nodes"].append(
                        Representation.get_substation_response_node(
                            dag.name, flow.instance_name, flow.substation_instance_name
                        )
                    )
                else:
                    representation["graph"]["nodes"].append(
                        Representation.get_flow_node(dag.name, flow.instance_name)
                    )

                for subscription in sorted(flow.subscriptions, key=lambda x: x.filter):
                    if isinstance(subscription, SubstationSubscription):
                        representation["graph"]["nodes"].append(
                            Representation.get_substation_subscription_node(
                                dag.name,
                                subscription.instance_name,
                                subscription.substation_instance_name,
                            )
                        )

            # Add all edges
            for edge in dag.edges:
                representation["graph"]["edges"].append(edge)

        return representation

    def substation_dag(self, dag_name: str, input_list: Optional[List[EdgeFrom]] = None) -> Any:
        substation_dag = self.dag(dag_name)
        if input_list is None:
            input_list = [self.input()]
        substation = Substation(self.name, dag_name)

        substation_request = substation_dag.substation_request(substation, input_list)
        substation_subscription = substation_dag.substation_subscription(
            self.name, dag_name, substation
        )
        substation_response = substation_dag.substation_response(
            substation, [substation_request, substation_subscription]
        )

        substation_dag.respond(substation_response)

        return substation_dag

    def modelhub(self, dag_name: str, input_list: Optional[List[EdgeFrom]] = None) -> Any:
        modelhub_dag = self.dag(dag_name)
        if input_list is None:
            input_list = [self.input()]
        modelhub = Modelhub(self.name, dag_name)

        modelhub_request = modelhub_dag.modelhub_request(modelhub, input_list)
        modelhub_subscription = modelhub_dag.modelhub_subscription(self.name, dag_name, modelhub)
        modelhub_response = modelhub_dag.modelhub_response(
            modelhub, [modelhub_request, modelhub_subscription]
        )

        def simplify_output(msg: Any) -> Any:
            simplified_output = modelhub.simplify_output(msg)
            output = msg.result(simplified_output)
            yield output

        simplified_response = modelhub_dag.task(
            simplify_output, [modelhub_response], instance_name="smpl-out"
        )

        modelhub_dag.respond(simplified_response)

        return modelhub_dag

    def bucket(self, bucket_name: str) -> Bucket:
        """
        Return a notify-capable bucket.

        Makes a network request to the Seaplane object store. Will
        attempt to create a new bucket if one doesn't exist, and may
        fail with an Exception if there is a service failure or if
        the named bucket exists but is not configured to send notifications.
        """

        notify = create_bucket_if_needed(self.name, bucket_name)
        b = Bucket(bucket_name, notify)
        self.buckets.add(b)
        return b

    def dag(self, dagname: str) -> Dag:
        """
        Creates and returns a new Dag. A Dag is a namespace and organizer
        for Flows, so typical applications will start with something like
        the following:

            app = App("my-app")
            dag = app.dag("my-dag")

            task1 = dag.task(work_function, [app.input()])
            ...

        Dags need globally Unique names - not just unique to an
        application, but globally across all of your Seaplane applications.
        Dag names will end up as part of task names in the applications
        you deploy.
        """
        prefixed_name = self.name_prefix + dagname
        if prefixed_name in self.dag_registry:
            raise SeaplaneError(
                f'there is already a dag named "{prefixed_name}" in application "{self.name}".'
                " The dags in an application must have unique names."
            )

        ret = Dag(prefixed_name)
        self.dag_registry[ret.name] = ret
        return ret

    def respond(self, tail: Union[Dag, Flow]) -> None:
        """
        Associate a given Flow or Dag as the "Final result" of an application.
        This binds the output of that Flow or Dag to the seaplane
        HTTP response endpoint, where it can be queried by systems
        outside of the Seaplane platform.
        """
        dag = tail.dag if isinstance(tail, Flow) else tail
        dag.edge(tail, self.output())

    def input(self) -> InEndpoint:
        """
        Returns a Subscription to the input endpoint for this Application.
        Tasks that subscribe to App.input() will get messages when
        external clients send data to the application request endpoint.
        """
        return self.input_endpoint

    def output(self) -> OutEndpoint:
        # Returns the out endpoint associated with the application.
        # A better way to send messages to the application endpoint
        # is to pass a Flow or Dag to App.respond()
        return self.output_endpoint

    def run(self) -> None:
        """
        Run the application as the main work of a process.

        Reads command line arguments and the INSTANCE_NAME environment variable. If INSTANCE_NAME
        is present, and no arguments are provided, will attempt to execute the associated task.
        """
        # This has a bunch of circular dependencies but the ergonomics of `app.run()`
        # seem to make it worthwhile.
        import sys

        import toml

        import seaplane.deploy
        import seaplane.pipes.executor
        import seaplane.pipes.status
        import seaplane.run_load_dotenv

        command = None
        if len(sys.argv) > 1:
            command = sys.argv[1]

        instance_name = os.getenv("INSTANCE_NAME")
        if command is None and instance_name:
            flow = None
            for dag in self.dag_registry.values():
                if instance_name in dag.flow_registry:
                    flow = dag.flow_registry[instance_name]
                    break

            if flow is None:
                raise RuntimeError(f"no task instance named {instance_name} found")

            seaplane.pipes.executor.execute(flow)

        elif command == "deploy":
            pyproject = toml.loads(open("pyproject.toml", "r").read())
            project_directory_name = pyproject["tool"]["poetry"]["name"]
            seaplane.deploy.deploy(self, project_directory_name)
        elif command == "deploy_debug":
            pyproject = toml.loads(open("pyproject.toml", "r").read())
            project_directory_name = pyproject["tool"]["poetry"]["name"]
            config.debug_mode()
            asyncio.run(seaplane.deploy.deploy_debug(self, project_directory_name))
        elif command == "destroy":
            seaplane.deploy.destroy(self)
        elif command == "destroy_debug":
            config.debug_mode()
            asyncio.run(seaplane.deploy.destroy_debug(self))
        elif command == "status":
            seaplane.pipes.status.status(self)
        else:
            if command is not None:
                print(f'command "{command}" not supported in this version')

            print(
                f"Usage: {sys.argv[0]} COMMAND"
                """

Commands:
    deploy    deploys your application to Seaplane
    destroy   halts your application on Seaplane
"""
            )
