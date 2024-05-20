import json
import os
from io import BytesIO

import requests
from langchain.document_loaders import PyMuPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from seaplane.apps import App
from seaplane.config import config
from seaplane.vector import Vector, vector_store

config.set_region("fra")


def input_task(input):
    data = json.loads(input.body)
    for url in data["urls"]:
        output = {}
        output["url"] = url
        print(f"outputting {url}")
        yield json.dumps(output)


# the processing task
def process_data(input):
    data = json.loads(input.body)

    # create text splitter
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=100,
        length_function=len,
        add_start_index=True,
    )

    #  load PDF
    url = data["url"]
    print(f"trying to load {url}")
    loader = PyMuPDFLoader(url)
    pages = loader.load_and_split(text_splitter)

    # add URL as metadata to enable source link in chat
    for page in pages:
        output = {}
        output["model"] = "embeddings"
        output["prompt"] = page.page_content
        output["metadata"] = {
            "page_content": page.page_content,
            "url": str(data["url"]),
            "metadata": page.metadata,
        }
        yield json.dumps(output)


def insert_vectors(input):
    data = json.loads(input.body)
    input_data = data["input_data"]
    vector = data["output"]
    metadata = input_data["metadata"]

    vector_store.create_index("northrift", if_not_exists=True)

    # create the vector representation the vector store understands
    vect = Vector(vector=vector, metadata=metadata)

    # insert vectors in vector store
    resp_dict = vector_store.insert("northrift", [vect])
    yield json.dumps(resp_dict)


def main():
    app = App("pdf-processor")
    dag = app.dag("pdf-dag")

    inputs = dag.task(input_task, [app.input()], instance_name="inputs")
    processed_data = dag.task(process_data, [inputs], instance_name="process")
    embeddings = app.modelhub("embed", [processed_data])
    inserted_vectors = dag.task(insert_vectors, [embeddings], instance_name="insert")

    app.respond(inserted_vectors)

    app.run()


if __name__ == "__main__":
    main()
