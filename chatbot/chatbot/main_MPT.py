from seaplane import app, task, start, config
from langchain.chains import ConversationalRetrievalChain
from seaplane.integrations.langchain import SeaplaneLLM, langchain_vectorstore
import logging


# the chat task that performs the document search and feeds them to the LLM
@task(type="inference", id="chat-task-northrift", model="MPT-30B")
def chat_task(data, model):
    # create vector store instance with langchain integration
    vectorstore = langchain_vectorstore(index_name="northrift")

    # Create the chain
    pdf_qa_hf = ConversationalRetrievalChain.from_llm(
        llm=SeaplaneLLM(model_specific_prompt=False),
        retriever=vectorstore.as_retriever(),
        return_source_documents=True,
    )

    # convert chat history to list of tuples
    chat_history = [tuple(item) for item in data["chat_history"]]

    # truncate chat history to max 5 messages
    if len(chat_history) > 5:
        chat_history = chat_history[-5:]

    # check if there is chat history
    if len(chat_history) > 0:
        chat_history_prompt = str(chat_history[-1][0])
    else:
        chat_history_prompt = "no question"

    # check if question is a follow up question to the last question
    history_result = model(
        {
            "prompt": f"Is this question: {data['query']} a follow up question to this question: {chat_history_prompt}. \
                Reply yes if it is and no if it is not. Only say yes if you are really sure it is.\
                follow up questions often have the following format 'and what about', 'tell me more', etc. \
                If it doesn't have a similar format it is likely not a follow up question and you should say no. \
                Only reply with yes or no\\n\\n### Response\\n",
            "max_output_length": 5000,
            "model_specific_prompt": False,
        }
    )

    follow_up = (
        history_result["choices"][0]["text"]["generated_text"]
        .lower()
        .split("# response")
    )

    # don't include chat history if not a follow up question
    if "no" in follow_up[-1].lower():
        chat_history = ""

    logging.error("check if follow up")
    logging.error(follow_up[-1].lower())
    logging.error(history_result)
    logging.error("--------------------------------")

    # answer the question using MPT-30B
    result = pdf_qa_hf({"question": data["query"], "chat_history": chat_history})

    logging.basicConfig(
        level=logging.ERROR, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    logging.error("inferenced result")
    logging.error(result)
    logging.error("--------------------------------")

    # get all the relevant source URLs
    urls = [source_doc.metadata["url"] for source_doc in result["source_documents"]]

    # return the answer and source documents
    return {
        "result": result["answer"].split("Helpful Answer:")[1],
        "source_documents": list(set(urls)),
    }


# HTTP enabled chat app
@app(id="chat-app-northrift", path="/chat", method=["POST", "GET"])
def chat_app(data):
    return chat_task(data)


start()
