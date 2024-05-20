import json
from typing import Any, Dict, Generator, List, Optional

from seaplane.substation import Substation


class Modelhub:
    """
    Class for a simplied wrapper around substation,
    simplifying both the input and output.
    """

    def __init__(self, app_name: str, dag_name: Optional[str] = None):
        self.substation = Substation(app_name, dag_name)

    def results_stream(self) -> str:
        return self.substation.results_stream()

    def get_model(self, model_name: str) -> str:
        return self.substation.get_model(model_name)

    def get_headers(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        return self.substation.get_headers(input_data)

    def make_request(self, input_data: Dict[str, Any]) -> Dict[Any, Any]:
        return self.substation.make_request(self.simplify_input(input_data))

    def get_response(self, msg: Generator[Any, None, None]) -> Generator[Any, None, None]:
        return self.substation.get_response(msg)

    def wrap_prompt_template(self, prompt: str, model: str, system_prompt: str = "") -> str:
        # wrap prompt in zephyr 7b prompt template
        if model == "zephyr-7b-beta":
            return f"""<|system|>\n{system_prompt}\n</s>\n<|user|>\n{prompt}</s>\n<|assistant|>"""

        # wrap prompt in Llama 70b prompt template
        elif model in ["llama-2-70b-chat", "llama-2-13b-chat", "llama-2-7b-chat"]:
            return f"""<s>[INST]<<SYS>>\n{system_prompt}\n<</SYS>>\n\n{prompt} [/INST]"""

        elif model in ["llama-3-70b", "llama-3-70b-instruct", "llama-3-8b", "llama-3-8b-instruct"]:
            return f"""<|start_header_id|>system<|end_header_id|>\n\n
{system_prompt}<|eot_id|>\n<|start_header_id|>user<|end_header_id|>\n\n{prompt}<|eot_id|>\n"""

        # wrap prompt in mystral 7b v0.2 prompt template
        elif model == "mistral-7b-instruct-v0.2":
            return f"""<s>[INST]{system_prompt} {prompt} [/INST]"""

        # wRap prompt in mixtral-8x7b-instruct-v0.1 prompt template
        elif model == "mistral-8x7b-instruct-v0.1":
            return f"""<s>[INST] {system_prompt} {prompt} [/INST] """

        # wrap prompt in starling 7b prompt template
        elif model == "starling-lm-7b-alpha":
            return f"""GPT4 User: {prompt}<|end_of_turn|>GPT4 Assistant:"""

        # wrap prompt in yi-34b prompt template
        elif model == "yi-34b":
            return f"""<|im_start|>system\n
{system_prompt}<|im_end|>
<|im_start|>user
{prompt}<|im_end|>
<|im_start|>assistant"""
        else:
            return prompt

    def simplify_input(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """This task takes a simplified input for models and a set of
        universal parameters and structures those into the required input per model.

        Args:
            msg (Message): the input message containing a JSON string of the following format:
                {
                    "model": str,
                    "prompt": str,
                    "system_prompt": str,
            return sub_out
                    "temperature" : float,
                    "max_new_tokens": int,
                    "min_new_tokens": int,
                    "top_p": float,
                    "top_k: int,
                    "stop_sequences": str,
                    "length_penalty": float,
                    "presence_penalty": int,
                    "frequency_penalty": int,
                    "repeat_penalty": float,
                    "use_prompt_template": bool,
                }

            model and prompt are required fields, anything else is optional and will take the
            models default value if not set. Use prompt template defaults to True and is only
            available to models that have a specific prompt template
        """

        # Load the input message
        output: Dict[str, Any] = {}
        output = input

        # Check if the required fields are present
        if not input.get("model"):
            raise ValueError("Model name is required")
        else:
            # get the model and prompt
            model = input["model"]
            output["model"] = input["model"]

        # check if this is an LLM call with a prompt
        if input.get("prompt"):
            # Check if the required fields are present
            if not input.get("prompt"):
                raise ValueError("Prompt is required")
            else:
                # wrap in prompt template if requested default is yes
                if model in [
                    "zephyr-7b-beta",
                    "mistral-7b-instruct-v0.2",
                    "mistral-8x7b-instruct-v0.1",
                    "starling-lm-7b-alpha",
                    "llama-2-70b-chat",
                    "llama-2-13b-chat",
                    "llama-2-7b-chat",
                    "llama-3-70b",
                    "llama-3-70b-instruct",
                    "llama-3-8b",
                    "llama-3-8b-instruct",
                ]:
                    output["prompt"] = self.wrap_prompt_template(input["prompt"], input["model"])
                else:
                    output["prompt"] = input["prompt"]

            # translate the default inputs to the model specific input parameters per model family
            # CODELLAMA MODEL FAMILY
            if model in [
                "codellama-7b-instruct",
                "codellama-13b-instruct",
                "codellama-70b-instruct",
                "codellama-7b-python",
                "codellama-13b-python",
                "codellama-34b-python",
                "codellama-70b-python",
            ]:
                # set max new tokens if provided by the users
                if input.get("max_new_tokens"):
                    output["max_tokens"] = input.get("max_new_tokens")

            elif model in ["embeddings", "embeddings-ext"]:
                if type(input.get("prompt")) is str:
                    output["text"] = input.get("prompt")
                elif type(input.get("prompt")) is List[str]:
                    output["text_batch"] = input.get("prompt")

            # ANTHROPIC MODEL FAMILY
            elif model in [
                "predictions-aws-anthropic-claude21",
                "predictions-aws-anthropic-claude-instant12",
                "predictions-aws-anthropic-claude3-haiku-20240307",
                "predictions-aws-anthropic-claude3-sonnet-20240229",
            ]:
                # construct prompt
                output["messages"] = [{"role": "user", "content": output["prompt"]}]

                # set max tokens if provided default to 512
                if input.get("max_new_tokens"):
                    output["max_tokens"] = input.get("max_new_tokens")
                else:
                    output["max_tokens"] = 512

                # set the system prompt if provided
                if input.get("system_prompt"):
                    output["system"] = input.get("system_prompt")

            # OPENAI MODEL FAMILY
            elif model in ["chat-azure-openai-gpt35-turbo16k", "chat-azure-openai-gpt4"]:
                # set the prompt in the required messaging structure
                output["messages"] = [{"role": "user", "content": output["prompt"]}]

                # set max tokens if provided
                if input.get("max_new_tokens"):
                    output["max_tokens"] = input.get("max_new_tokens")

                # set the stop sequence if provided
                if input.get("stop_sequence"):
                    output["stop"] = input.get("stop_sequence")

            # GOOGLE MODEL FAMILY
            elif model in [
                "predictions-google-gemini10-pro",
                "predictions-google-gemini10-pro-vision",
            ]:
                # set the prompt in the required format
                output["contents"] = {
                    "role": "user",
                    "parts": {"text": output["prompt"]},
                }

                # initalize generation config
                output["generation_config"] = {}

                # set max tokens if provided
                if input.get("max_new_tokens"):
                    output["generation_config"]["maxOutputTokens"] = input.get("max_new_tokens")

                # set temperature if provided
                if input.get("temperature"):
                    output["generation_config"]["temperature"] = input.get("temperature")

                # set top p if provided
                if input.get("top_p"):
                    output["generation_config"]["topP"] = input.get("top_p")

                # set top k if provided
                if input.get("top_k"):
                    output["generation_config"]["topK"] = input.get("top_k")

                # set stop sequences if provided
                if input.get("stop_sequences"):
                    output["generation_config"]["stopSequences"] = input.get("stop_sequences")

            # STARLING 7B MODEL FAMILY
            elif model == "starling-lm-7b-alpha":
                # set the max_tokens if provided
                if input.get("max_new_tokens"):
                    output["max_tokens"] = input.get("max_new_tokens")

            # FALCON MODEL FAMILY
            elif model == "falcon-40b-instruct":
                # set the max_tokens if provided
                if input.get("max_new_tokens"):
                    output["max_length"] = input.get("max_new_tokens")

            # VICUNA MODEL FAMILY
            elif model == "vicuna-13b":
                # set the max_tokens if provided
                if input.get("max_new_tokens"):
                    output["max_length"] = input.get("max_new_tokens")

            # PHI MODEL FAMILY
            elif model == "phi2":
                # set the max_tokens if provided
                if input.get("max_new_tokens"):
                    output["max_length"] = input.get("max_new_tokens")

            elif model == "predictions-aws-ai21-jurassic2-ultra":
                # set the max_tokens if provided
                if input.get("max_new_tokens"):
                    output["maxTokens"] = input.get("max_new_tokens")

                # set the top p if provided
                if input.get("top_p"):
                    output["topP"] = input.get("top_p")

                # set presencePenalty if provided
                if input.get("presence_penalty"):
                    output["presencePenalty"] = input.get("presence_penalty")

                # set frequencey penalty if provided
                if input.get("frequency_penalty"):
                    output["frequencyPenalty"] = input.get("frequency_penalty")

                # set the repetition penalty if provided
                if input.get("repeat_penalty"):
                    output["countPenalty"] = input.get("repeat_penalty")

            elif model == "predictions-aws-mistral-mixtral-8x7b-instruct01":
                # set the max_tokens if provided
                if input.get("max_new_tokens"):
                    output["max_tokens"] = input.get("max_new_tokens")

            # WIZARD CODER FAMILY
            elif model == "wizardcoder-34b-v1.0":
                # set the max_tokens if provided
                if input.get("max_new_tokens"):
                    output["max_length"] = input.get("max_new_tokens")
            # failover to substation safely
            else:
                output = input
        # failover to substation safely
        else:
            output = input

        # return the results
        return output

    def simplify_output(self, msg: Any) -> Any:
        """This task takes any LLM input and simplifies the output into a single JSON:

        {
        "input_data" : object,
        "output" : str,
        }

        Args:
            msg (Message): the input message to the task which should
            come directly from a Substation instance.
        """

        # load the input data and get the model name
        data = json.loads(msg.body)

        if data["input_data"]["model"]:
            model = data["input_data"]["model"]

            # create an output object and add the input data that was provided to substation
            output = {"input_data": data["input_data"]}

            # THIRD PARTY MODELS
            if model in [
                "zephyr-7b-beta",
                "mistral-7b-instruct-v0.1",
                "mistral-7b-instruct-v0.2",
                "mixtral-8x7b-instruct-v0.1",
                "starling-lm-7b-alpha",
                "yi-34b-chat",
                "yi-6b",
                "falcon-40b-instruct",
                "vicuna-13b",
                "phi-2",
                "olmo-7b",
                "llama-2-7b-chat",
                "llama-2-13b-chat",
                "llama-2-70b-chat",
                "llama-3-70b",
                "llama-3-70b-instruct",
                "llama-3-8b",
                "llama-3-8b-instruct",
                "codellama-7b-instruct",
                "codellama-7b-python",
                "codellama-13b-instruct",
                "codellama-34b-instruct",
                "codellama-34b-python",
                "codellama-70b",
                "codellama-70b-instruct",
                "codellama-70b-python",
                "wizardcoder-34b-v1.0",
            ]:
                # get the relevant output data and add to output
                output["output"] = data.get("output")

            # MISTRAL MODEL FAMILY
            if model in [
                "predictions-aws-anthropic-claude21",
                "predictions-aws-anthropic-claude-instant12",
                "predictions-aws-anthropic-claude3-haiku-20240307",
                "predictions-aws-anthropic-claude3-sonnet-20240229",
            ]:
                # get the output from the model
                output["output"] = data["request"]["output"]["content"][0]["text"]

            # AWS MODELS
            if model == "predictions-aws-ai21-jurassic2-ultra":
                output["output"] = data["request"]["output"]["completions"][0]["data"]["text"]

            # MIXTRAL
            if model == "predictions-aws-mistral-mixtral-8x7b-instruct01":
                output["output"] = data["request"]["output"]["outputs"][0]["text"]

            # GOOGLE MODELS
            if model in [
                "predictions-google-gemini10-pro",
                "predictions-google-gemini10-pro-vision",
            ]:
                # join all the output together to create a single string
                final_output = ""
                for text_output in data["request"]["output"]:
                    final_output += text_output["candidates"][0]["content"]["parts"][0]["text"]

                output["output"] = final_output

            # OPENAI MODELS
            if model in ["chat-azure-openai-gpt35-turbo16k", "chat-azure-openai-gpt4"]:
                output["output"] = data["request"]["output"]["choices"][0]["message"]["content"]

            # EMBEDDINGS
            if model in ["embeddings", "embeddings-ext"]:
                output["output"] = data["output"][0]["embedding"]

            # return the results
            return json.dumps(output)

        else:
            return
