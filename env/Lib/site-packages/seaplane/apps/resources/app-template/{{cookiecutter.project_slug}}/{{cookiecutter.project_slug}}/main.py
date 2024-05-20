from seaplane.apps import App

# Remember to add your SEAPLANE_API_KEY in .env file


def hello_world(message):
    return "hello world"


# An "App" is a bunch of tasks that you'll deploy together,
# along with the endpoints you can use to communicate with your
# system once it's deployed to the Seaplane infrastructure.
app = App("hello-world")

# A "dag" is a way to organize work. Dags change the names of tasks,
# so you can use the similar collections of tasks in different places
# in your application.
dag = app.dag("tasks")

# a Task is the fundamental unit of work for a Seaplane application.
# It's the environment you'll use to run your functions.
hello = dag.task(hello_world, [app.input()], instance_name="hello")

# You can send the results of a task back to the appliction response
# endpoint, for reading by clients outside of the Seaplane platform.
app.respond(hello)

# When you've constructed your app, call `app.run()`. On
# your workstation, this will make the app available for
# deployment. On the Seaplane infrastructure, this will
# run the appropriate task.
app.run()
