import json
import os
import time
import requests
import streamlit as st
# from dotenv import load_dotenv

# Load environment variables from .env file
# load_dotenv()

# Retrieve API key from environment variables
API_KEY = os.getenv("SEAPLANE_API_KEY")

# Define the main structure of the page
st.set_page_config(layout="wide")
st.markdown(
    """
    <style>
    #MainMenu {visibility: hidden;}

    
   .st-emotion-cache-1629p8f h1{
        transform: translate(15%, 0%); 
   }

   /* welcome text */
    .st-emotion-cache-4oy321{
        width:800px;
        padding:1rem;
        left:0;
        transform: translate(0%, 20%); 
    }

    /* chat/text input */
    .st-emotion-cache-qdbtli {
        transform: translate(-60%, 0%);
        width: 600px;
        left:5%;
        z-index:-1;
    }
    /* user input-result */
    .st-emotion-cache-1c7y2kd{
        transform: translate(1%, 10%);
        padding:1rem;
        width:50%; 
    }

    .right{
    position:absolute;
    width:200px;
    }
    /* dec */
    .description{
        background-color:;
        width:700px;
        height:95vh;
        transform: translate(0%, -10%); 
        position:fixed;
        z-index:1;
        right:0;
        top:16%;
        border-radius:20px;
    }
    .st-emotion-cache-1jicfl2 {
        width: 100%;
        padding: 2rem;
        width:500px;
        height: 100vh;
    }
    .footer {
        font-size: 15px;
        color: white;
        padding: 10px;
        bottom: 0;
        width: 100%;
        text-align: center;
    }
    .ai-footer {
        font-size: 50px;
        font-weight: 400;
        position: absolute;
        transform: translate(10%, -100%); 
        color: #fff;
    }
    .image-container {
        display: flex;
        justify-content: center;
        align-items: center;
        background-color: #f0f0f0;
        border-radius: 10px;
        height: 300px;
        box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# get Seaplane access token
def get_token(api_key):
    url = "https://flightdeck.cplane.cloud/identity/token"
    headers = {"Authorization": "Bearer " + api_key}

    response = requests.post(url, headers=headers)
    return response

def post_request(api_key, prompt, history):
    # construct data component with your name
    data = {"query": prompt, "chat_history": history}

    # convert to json
    json_data = json.dumps(data)

    # get the token
    response = get_token(api_key)

    # Set the token and URL
    token = response.text
    url = "https://carrier.cplane.cloud/v1/endpoints/chatbot/request"

    # Set the headers
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/octet-stream",
    }

    # Make the POST request
    api_response = requests.post(url, headers=headers, data=json_data)

    # return the response
    return api_response.content

def get_request(api_key, request_id):
    # get the token
    response = get_token(api_key)

    # Set the token and URL
    token = response.text
    url = (
        "https://carrier.cplane.cloud/v1/endpoints/chatbot/response/"
        + request_id
        + "/archive"
    )
    params = {"pattern": ".>", "format": "json_array"}

    # Set the headers
    headers = {"Authorization": f"Bearer {token}"}

    # Make the POST request
    api_response = requests.get(url, headers=headers, params=params)

    # Print the response
    return api_response.content

st.title("Kilimo Chatbot")

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = []
    st.session_state.history = []

    # start with a welcome message
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": (
                "Welcome to the Kilimo chatbot. I can answer any questions you have about the"
                " information on https://kilimo.go.ke/ but I am still an AI so make sure to check"
                " the details in the linked documents"
            ),
        }
    )

# Display chat messages from history on app rerun
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Chat input field at the bottom
st.markdown('<div class="chat-inputs" style="width:50%;">', unsafe_allow_html=True)
prompt = st.chat_input("How can I help you?")
st.markdown('</div>', unsafe_allow_html=True)

# React to user input
if prompt:
    # Display user message in chat message container
    st.chat_message("user").markdown(prompt)
    # Add user message to chat history
    st.session_state.messages.append({"role": "user", "content": prompt})

    # submit question to seaplane
    r = post_request(API_KEY, prompt, st.session_state.history)
    print(r)

    try:
        # get the request id from the response
        data = json.loads(r)
        request_id = data["request_id"]
        print(request_id)

        # counter to see how long we are waiting
        counter = 0

        # GET request to get the result in loop to anticipate longer processing times
        while True:
            get_result = get_request(API_KEY, request_id)
            answer = json.loads(get_result.decode())
            print(answer)

            # if more than 1 get request show the user we are busy searching
            if counter == 1:
                with st.chat_message("assistant"):
                    st.markdown(
                        "Gathering insights from hundreds of publications, manuals, guides, and a"
                        " wealth of ministry resources... one moment please."
                    )
                    st.session_state.messages.append(
                        {
                            "role": "assistant",
                            "content": (
                                "Gathering insights from hundreds of publications, manuals,"
                                " guides, and a wealth of ministry resources... one moment please."
                            ),
                        }
                    )

            # update counter
            counter += 1

            # update response once status is completed
            # if answer["status"] == "completed":
            if len(answer) == 1:
                response = answer[0]["result"]
                source_docs = answer[0]["source_documents"]

                # string to store the source docs in
                source_response = (
                    "Take a look at the following documents to learn more: \n"
                )

                # create a list of documents to display
                for idx, text in enumerate(list(set(source_docs))):
                    source_response += f" - [{source_docs[idx]}]({source_docs[idx]})\n"

                break

    except Exception as error:
        # show the error in the chat if we fail
        response = error

    # Display assistant response in chat message container
    with st.chat_message("assistant"):
        # combine the answer and source documents as the response
        combined_response = response  # + "\n\n" + source_response
        st.markdown(combined_response)

        # store the history
        st.session_state.history.append((prompt, response))

    # Add assistant response to chat history
    st.session_state.messages.append(
        {"role": "assistant", "content": combined_response}
    )


st.markdown(
    '<div class="description" style="color: black; text-align:center;">'
    '<h3 style="border:1px solid white; border-radius: .5em;">Feed & News</h3>'
    '<p>hello</p></div>', 
    unsafe_allow_html=True
)


# with st.container():
#     st.title("Home Page")
#     st.write("Welcome to the home page!")

# st.markdown(
#     '<div class="right" style="background-color:#0d1117;">', 
#     unsafe_allow_html=True
# )
# st.markdown(
#     '<div class="description" style="color: black; text-align:center;">'
#     '<h3 style="border:1px solid white; border-radius: .5em;">Feed & News</h3></div>', 
#     unsafe_allow_html=True
# )
# # st.markdown(
# #     """
# #     <div class="image-container">
# #         <img src="https://img.freepik.com/premium-photo/falling-coffee-beans-dark-with-copy-space_88281-1264.jpg?w=360" width="790">
# #     </div>
# #     """,
# #     unsafe_allow_html=True
# # )
# st.markdown(
#     '<p class="ai-footer">'
#     'The future of farming support, empowered by AI.</p>', 
#     unsafe_allow_html=True
# )
# st.markdown(
#     '<div class="footer" style="color: black;">Scroll for More</div>', 
#     unsafe_allow_html=True
# )
# st.markdown('</div>', unsafe_allow_html=True)
