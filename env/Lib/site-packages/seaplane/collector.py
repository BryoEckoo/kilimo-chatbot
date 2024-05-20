import json
from typing import Any, Generator, List

from seaplane.gen.carrier import ApiException

from seaplane.kv import KeyValueStorageAPI
from seaplane.pipes import App, EdgeFrom
from seaplane.pipes.executor import Result

COLLECT_STORE = "_SP_COLLECT_"

# TODO: add option to compress messages (on stream AND kv store)


class Collector:
    """
    Class for handling debatching and collecting
    """

    def __init__(self, name: str):
        self.collect_store = f"{COLLECT_STORE}{name}"

    def debatch(self, msg: Any, input_list: List[Any]) -> List[Result]:
        """
        Splits a list into separate messages to be yielded in your task

        msg is the input to the task (to preserve metadata)

        input_list is a python List (e.g., of strings or JSON objects)

        Example:

        for ret in collector.debatch(msg, input_data["input"]):
            yield ret
        """
        total = len(input_list)
        print(f"processing list with {total} items...")
        msg_list: List[Result] = []
        for count in range(total):
            # this should cover anything in a JSON array deserialized with json.loads()
            if type(input_list[count]) is str:
                ret_bytes = input_list[count].encode()
            elif type(input_list[count]) is dict:
                ret_bytes = json.dumps(input_list[count]).encode()
            ret = msg.result(ret_bytes)
            ret.meta["_seaplane_batch_total"] = total
            ret.meta["_seaplane_debatch_hierarchy"] = msg.meta["_seaplane_batch_hierarchy"]
            msg_list.append(ret)
        return msg_list

    def collect_batch(self, msg: Any) -> Generator[Any, None, None]:
        """
        Use this task to collect the responses and yield a JSON-compatible list of strings.

        Note: if your debatched data is binary (images, pickles, etc.) you can store them in
        object or KV store and yield the data (url or object name) necessary for retrieval.
        This is also preferred if the data is very large, to avoid message size limits.
        """

        # lots of stuff to preserve message hierarchy for later output
        request_id = msg.meta["_seaplane_request_id"]
        batch_total = int(msg.meta.get("_seaplane_batch_total", "0"))
        batch_hierarchy = msg.meta["_seaplane_batch_hierarchy"]
        debatch_hierarchy = msg.meta.get("_seaplane_debatch_hierarchy", "")
        message_id = f"{request_id}{batch_hierarchy.replace('.','_')}"

        print(f"received {message_id}/{batch_total}")

        kv = KeyValueStorageAPI()
        kv.set_key(self.collect_store, message_id, msg.body)

        list_key = f"list_{request_id}{debatch_hierarchy.replace('.','_')}"
        stored_keys = eval(kv.get(self.collect_store, list_key, b"[]"))
        stored_keys.append(message_id)

        # once we have all of the keys stored we can put it all together
        if len(stored_keys) == batch_total:
            print(f"collecting request_id {request_id}...")
            collected = []
            debatch_list = batch_hierarchy.split(".")[len(debatch_hierarchy.split(".")) :]
            for i in range(1, batch_total + 1):
                debatch_list[0] = str(i)
                key = f"{request_id}{debatch_hierarchy.replace('.','_')}_{'_'.join(debatch_list)}"
                collected.append(kv.get_key(self.collect_store, key).decode())
            print(collected)
            ret = msg.result(json.dumps(collected))
            debatch_list[0] = str(1)
            new_batch_hierarchy = f"{debatch_hierarchy}.{'.'.join(debatch_list)}.1"
            ret.override_batch_hierarchy(new_batch_hierarchy)
            yield ret
            for key in stored_keys:
                kv.delete_key(self.collect_store, key)
            kv.delete_key(self.collect_store, list_key)
        else:
            kv.set_key(self.collect_store, list_key, str(stored_keys).encode())
            yield


def collect_inputs_dag(app: App, name: str, input_list: List[EdgeFrom]) -> Any:
    dag = app.dag(name)

    def multiple_in(msg: Any) -> Any:
        subject = msg.meta["nats_subject"]
        filters = [
            s.filter.replace(".*", "").replace(".>", "")
            for s in msg.flow.subscriptions
            if s.matches_subject(subject)
        ]
        possibilities = [
            s.filter.replace(".*", "").replace(".>", "") for s in msg.flow.subscriptions
        ]

        tasks = [s.replace(".", "_").replace("-", "_") for s in possibilities]
        input_task = filters[0].replace(".", "_").replace("-", "_")
        if tasks and input_task:
            print(f"Message from {input_task} of {tasks}")

            kv = KeyValueStorageAPI()
            collect_store = f"{COLLECT_STORE}{app.name}"
            request_id = msg.meta["_seaplane_request_id"]
            dag_instance_name = msg.flow.instance_name.replace(".", "_")
            input_task_msg_id = f"{request_id}_{dag_instance_name}_{input_task}"

            kv.set_key(collect_store, input_task_msg_id, msg.body)

            expected_keys = [f"{request_id}_{dag_instance_name}_{task}" for task in tasks]

            stored_keys = []
            output = []
            for key in expected_keys:
                try:
                    value = json.loads(kv.get_key(collect_store, key))
                    stored_keys.append(key)
                    output.append(value)
                except ApiException:
                    break

            if stored_keys == expected_keys:
                ret = msg.result(json.dumps(output).encode())
                yield ret
                for key in stored_keys:
                    print(key)
                    kv.delete_key(collect_store, key)
            else:
                yield

    collected = dag.task(multiple_in, input_list)

    dag.respond(collected)

    return dag
