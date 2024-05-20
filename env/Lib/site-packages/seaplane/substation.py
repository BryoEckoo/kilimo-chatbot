import json
import os
from typing import Any, Dict, Generator, Optional

import requests

from seaplane.config import config
from seaplane.errors import SeaplaneError
from seaplane.kv import kv_store
from seaplane.logs import log
from seaplane.object import ObjectStorageAPI

SUBSTATION_RESULTS_STREAM = "_SEAPLANE_AI_RESULTS"


class Substation:
    """
    Class for handling requests to and responses from Substation.

    See docstring for `make_request` for expected input and list of supported models.
    """

    def __init__(self, app_name: str, dag_name: Optional[str] = None):
        self.app_division = f"{app_name}"
        if dag_name is not None:
            self.app_division += f"-{dag_name}"
        self.request_store = f"_SP_REQUEST_{app_name}"
        self.response_store = f"_SP_RESPONSE_{app_name}"

    def results_stream(self) -> str:
        """
        Returns a string with the substation results stream
        """
        return SUBSTATION_RESULTS_STREAM

    models: Dict[str, Any] = {
        # Seaplane internal embeddings (CPU)
        "embeddings": {
            "model_name": [
                "sentence-transformers/all-mpnet-base-v2-cpu",
                "all-mpnet-base-v2",
                "embeddings",
            ],
            "headers": {"X-Model": "sentence-transformers/all-mpnet-base-v2-cpu"},
            "params": [
                "text",
                "text_batch",
            ],
            "url_path": "seaplane-predictions",
            "default_iata": "sjc",
        },
        # Seaplane internal embeddings (GPU)
        # "embeddings-gpu": {
        #     "model_name": ["sentence-transformers/all-mpnet-base-v2", "embeddings-gpu"],
        #     "headers": {"X-Model": "sentence-transformers/all-mpnet-base-v2"},
        #     "params": [
        #         "text",
        #         "text_batch",
        #     ],
        #     "url_path": "seaplane-predictions",
        #     "default_iata": "jfk",
        # },
        # Replicate embeddings (https://replicate.com/replicate/all-mpnet-base-v2)
        "embeddings-ext": {
            "model_name": [
                "replicate/all-mpnet-base-v2",
                "embeddings-ext",
            ],
            "headers": {
                "X-Version": "b6b7585c9640cd7a9572c6e129c9549d79c9c31f0d3fdce7baac7c67ca38f305"
            },
            "params": [
                "text",
                "text_batch",
            ],
            "url_path": "predictions",
        },
        # Zephyr-7B (https://replicate.com/tomasmcm/zephyr-7b-beta)
        "zephyr-7b-beta": {
            "model_name": [
                "tomasmcm/zephyr-7b-beta",
                "zephyr-7b-beta",
                "zephyr-7b",
            ],
            "headers": {
                "X-Version": "961cd6665b811d0c43c0b9488b6dfa85ff5c7bfb875e93b4533e4c7f96c7c526"
            },
            "params": [
                "prompt",
                "max_new_tokens",
                "temperature",
                "top_p",
                "top_k",
                "presence_penalty",
            ],
            "url_path": "predictions",
        },
        # Mistral-7b-instruct-v0.1 (https://replicate.com/mistralai/mistral-7b-instruct-v0.1)
        "mistral-7b-instruct-v0.1": {
            "model_name": [
                "mistralai/mistral-7b-instruct-v0.1",
                "mistral-7b-instruct-v0.1",
            ],
            "headers": {
                "X-Version": "5fe0a3d7ac2852264a25279d1dfb798acbc4d49711d126646594e212cb821749"
            },
            "params": [
                "prompt",
                "max_new_tokens",
                "min_new_tokens",
                "temperature",
                "top_p",
                "top_k",
                "repetition_penalty",
                "stop_sequences",
                "seed",
                "prompt_template",
            ],
            "url_path": "predictions",
        },
        # Mistral-7b-instruct-v0.2 (https://replicate.com/mistralai/mistral-7b-instruct-v0.2)
        "mistral-7b-instruct-v0.2": {
            "model_name": [
                "mistralai/mistral-7b-instruct-v0.2",
                "mistral-7b-instruct-v0.2",
                "mistral-7b-instruct",
            ],
            "headers": {
                "X-Version": "79052a3adbba8116ebc6697dcba67ad0d58feff23e7aeb2f103fc9aa545f9269"
            },
            "params": [
                "prompt",
                "max_new_tokens",
                "min_new_tokens",
                "temperature",
                "top_p",
                "top_k",
                "repetition_penalty",
                "stop_sequences",
                "seed",
                "prompt_template",
            ],
            "url_path": "predictions",
        },
        # mixtral-8x7b-instruct-v0.1 (https://replicate.com/mistralai/mixtral-8x7b-instruct-v0.1)
        "mixtral-8x7b-instruct-v0.1": {
            "model_name": [
                "mistralai/mixtral-8x7b-instruct-v0.1",
                "mixtral-8x7b-instruct-v0.1",
            ],
            "headers": {
                "X-Version": "cf18decbf51c27fed6bbdc3492312c1c903222a56e3fe9ca02d6cbe5198afc10"
            },
            "params": [
                "prompt",
                "max_new_tokens",
                "temperature",
                "top_p",
                "top_k",
                "presence_penalty",
                "frequency_penalty",
                "prompt_template",
            ],
            "url_path": "predictions",
        },
        # BAAI/bge-large-en-v1.5 (https://replicate.com/nateraw/bge-large-en-v1.5)
        "bge-large-en-v1.5": {
            "model_name": [
                "nateraw/bge-large-en-v1.5",
                "bge-large-en-v1.5",
            ],
            "headers": {
                "X-Version": "9cf9f015a9cb9c61d1a2610659cdac4a4ca222f2d3707a68517b18c198a9add1"
            },
            "params": [
                "texts",
                "batch_size",
                "normalize_embeddings",
            ],
            "url_path": "predictions",
        },
        # tomasmcm/starling-lm-7b-alpha (https://replicate.com/tomasmcm/starling-lm-7b-alpha)
        "starling-lm-7b-alpha": {
            "model_name": ["tomasmcm/starling-lm-7b-alpha", "starling-lm-7b-alpha"],
            "headers": {
                "X-Version": "1cee13652378fac04fe10dedd4c15d3024a0958c3e52f97a1aa7c4d05b99ef99"
            },
            "params": [
                "prompt",
                "max_tokens",
                "presence_penalty",
                "frequency_penalty",
                "temperature",
                "top_p",
                "top_k",
                "stop",
            ],
            "url_path": "predictions",
        },
        # 01-ai/yi-34b-chat (https://replicate.com/01-ai/yi-34b-chat)
        "yi-34b-chat": {
            "model_name": ["01-ai/yi-34b-chat", "yi-34b-chat"],
            "headers": {
                "X-Version": "914692bbe8a8e2b91a4e44203e70d170c9c5ccc1359b283c84b0ec8d47819a46"
            },
            "params": [
                "prompt",
                "max_new_tokens",
                "temperature",
                "top_p",
                "top_k",
                "repetition_penalty",
                "prompt_template",
            ],
            "url_path": "predictions",
        },
        # 01-ai/yi-6b (https://replicate.com/01-ai/yi-6b)
        "yi-6b": {
            "model_name": ["01-ai/yi-6b", "yi-6b"],
            "headers": {
                "X-Version": "d302e64fad6b4d85d47b3d1ed569b06107504f5717ee1ec12136987bec1e94f1"
            },
            "params": [
                "prompt",
                "max_new_tokens",
                "temperature",
                "top_p",
                "top_k",
                "presence_penalty",
                "frequency_penalty",
                "prompt_template",
            ],
            "url_path": "predictions",
        },
        # joehoover/falcon-40b-instruct ()
        "falcon-40b-instruct": {
            "model_name": ["joehoover/falcon-40b-instruct", "falcon-40b-instruct"],
            "headers": {
                "X-Version": "7d58d6bddc53c23fa451c403b2b5373b1e0fa094e4e0d1b98c3d02931aa07173"
            },
            "params": [
                "prompt",
                "max_length",
                "temperature",
                "top_p",
                "repetition_penalty",
                "length_penalty",
                "no_repeat_ngram_size",
                "stop_sequences",
                "seed",
            ],
            "url_path": "predictions",
        },
        # replicate/vicuna-13b (https://replicate.com/replicate/vicuna-13b)
        "vicuna-13b": {
            "model_name": ["replicate/vicuna-13b", "vicuna-13b"],
            "headers": {
                "X-Version": "6282abe6a492de4145d7bb601023762212f9ddbbe78278bd6771c8b3b2f2a13b"
            },
            "params": [
                "prompt",
                "max_length",
                "temperature",
                "top_p",
                "repetition_penalty",
                "seed",
            ],
            "url_path": "predictions",
        },
        # lucataco/phi-2 (https://replicate.com/lucataco/phi-2)
        "phi-2": {
            "model_name": ["lucataco/phi-2", "phi-2"],
            "headers": {
                "X-Version": "740618b0c24c0ea4ce5f49fcfef02fcd0bdd6a9f1b0c5e7c02ad78e9b3b190a6"
            },
            "params": [
                "prompt",
                "max_length",
            ],
            "url_path": "predictions",
        },
        # lucataco/olmo-7b (https://replicate.com/lucataco/olmo-7b)
        "olmo-7b": {
            "model_name": ["lucataco/olmo-7b", "olmo-7b"],
            "headers": {
                "X-Version": "25f57f6a97c974b52f8efe11bc0898b6502cc0ba7cc2db4d606e95ced412c31b"
            },
            "params": [
                "prompt",
                "max_new_tokens",
                "top_p",
                "top_k",
            ],
            "url_path": "predictions",
        },
        # meta/llama-2-7b-chat (https://replicate.com/meta/llama-2-7b-chat)
        "llama-2-7b-chat": {
            "model_name": ["meta/llama-2-7b-chat", "llama-2-7b-chat"],
            "headers": {
                "X-Version": "f1d50bb24186c52daae319ca8366e53debdaa9e0ae7ff976e918df752732ccc4"
            },
            "params": [
                "prompt",
                "system_prompt",
                "max_new_tokens",
                "min_new_tokens",
                "temperature",
                "top_p",
                "top_k",
                "repetition_penalty",
                "stop_sequences",
                "seed",
            ],
            "url_path": "predictions",
        },
        # meta/llama-2-13b-chat (https://replicate.com/meta/llama-2-13b-chat)
        "llama-2-13b-chat": {
            "model_name": ["meta/llama-2-13b-chat", "llama-2-13b-chat"],
            "headers": {
                "X-Version": "56acad22679f6b95d6e45c78309a2b50a670d5ed29a37dd73d182e89772c02f1"
            },
            "params": [
                "prompt",
                "system_prompt",
                "max_new_tokens",
                "min_new_tokens",
                "temperature",
                "top_p",
                "top_k",
                "stop_sequences",
                "seed",
            ],
            "url_path": "predictions",
        },
        # meta/llama-2-70b-chat (https://replicate.com/meta/llama-2-70b-chat)
        "llama-2-70b-chat": {
            "model_name": [
                "llama-2-70b-chat",
                "meta/llama-2-70b-chat",
                "llama-2 (70b)",
            ],
            "headers": {
                "X-Version": "02e509c789964a7ea8736978a43525956ef40397be9033abf9fd2badfe68c9e3"
            },
            "params": [
                "prompt",
                "system_prompt",
                "max_new_tokens",
                "min_new_tokens",
                "temperature",
                "top_p",
                "top_k",
                "stop_sequences",
                "seed",
            ],
            "url_path": "predictions",
        },
        # CodeLlama-7b-Instruct (https://replicate.com/meta/codellama-7b-instruct)
        "codellama-7b-instruct": {
            "model_name": ["meta/codellama-7b-instruct", "codellama-7b-instruct"],
            "headers": {
                "X-Version": "aac3ab196f8a75729aab9368cd45ea6ad3fc793b6cda93b1ded17299df369332"
            },
            "params": [
                "prompt",
                "system_prompt",
                "max_tokens",
                "temperature",
                "top_p",
                "top_k",
                "frequency_penalty",
                "presence_penalty",
                "repeat_penalty",
            ],
            "url_path": "predictions",
        },
        # CodeLlama-7b-Python (https://replicate.com/meta/codellama-7b-python)
        "codellama-7b-python": {
            "model_name": ["meta/codellama-7b-python", "codellama-7b-python"],
            "headers": {
                "X-Version": "c45ef9d988c417116d6ce1691ed5749fb5579d269b8f30aaf275b46c03a1c45e"
            },
            "params": [
                "prompt",
                "max_tokens",
                "temperature",
                "top_p",
                "top_k",
                "frequency_penalty",
                "presence_penalty",
                "repeat_penalty",
            ],
            "url_path": "predictions",
        },
        # CodeLlama-13b-Instruct (https://replicate.com/meta/codellama-13b-instruct)
        "codellama-13b-instruct": {
            "model_name": [
                "meta/codellama-13b-instruct",
                "codellama-13b-instruct",
            ],
            "headers": {
                "X-Version": "a5e2d67630195a09b96932f5fa541fe64069c97d40cd0b69cdd91919987d0e7f"
            },
            "params": [
                "prompt",
                "system_prompt",
                "max_tokens",
                "temperature",
                "top_p",
                "top_k",
                "frequency_penalty",
                "presence_penalty",
                "repeat_penalty",
            ],
            "url_path": "predictions",
        },
        # CodeLlama-34b-Instruct (https://replicate.com/meta/codellama-34b-instruct)
        "codellama-34b-instruct": {
            "model_name": ["meta/codellama-34b-instruct", "codellama-34b-instruct"],
            "headers": {
                "X-Version": "eeb928567781f4e90d2aba57a51baef235de53f907c214a4ab42adabf5bb9736"
            },
            "params": [
                "prompt",
                "system_prompt",
                "max_tokens",
                "temperature",
                "top_p",
                "top_k",
                "frequency_penalty",
                "presence_penalty",
                "repeat_penalty",
            ],
            "url_path": "predictions",
        },
        # CodeLlama-34b-Python (https://replicate.com/meta/codellama-34b-python)
        "codellama-34b-python": {
            "model_name": [
                "meta/codellama-34b-python",
                "codellama-34b-python",
            ],
            "headers": {
                "X-Version": "e4cb69045bdb604862e80b5dd17ef39c9559ad3533e9fd3bd513cc68ff023656"
            },
            "params": [
                "prompt",
                "max_tokens",
                "temperature",
                "top_p",
                "top_k",
                "frequency_penalty",
                "presence_penalty",
                "repeat_penalty",
            ],
            "url_path": "predictions",
        },
        # CodeLlama-70b (https://replicate.com/meta/codellama-70b)
        "codellama-70b": {
            "model_name": ["meta/codellama-70b", "codellama-70b"],
            "headers": {
                "X-Version": "69090e16762083aee59c9df30ccf0865b501672925d9152b8f4445bd57e730fa"
            },
            "params": [
                "prompt",
                "max_tokens",
                "temperature",
                "top_p",
                "top_k",
                "frequency_penalty",
                "presence_penalty",
                "repeat_penalty",
            ],
            "url_path": "predictions",
        },
        # CodeLlama-70b-Instruct (https://replicate.com/meta/codellama-70b-instruct)
        "codellama-70b-instruct": {
            "model_name": [
                "meta/codellama-70b-instruct",
                "codellama-70b-instruct",
            ],
            "headers": {
                "X-Version": "a279116fe47a0f65701a8817188601e2fe8f4b9e04a518789655ea7b995851bf"
            },
            "params": [
                "prompt",
                "system_prompt",
                "max_tokens",
                "temperature",
                "top_p",
                "top_k",
                "frequency_penalty",
                "presence_penalty",
                "repeat_penalty",
            ],
            "url_path": "predictions",
        },
        # CodeLlama-70b-Python (https://replicate.com/meta/codellama-70b-python)
        "codellama-70b-python": {
            "model_name": [
                "meta/codellama-70b-python",
                "codellama-70b-python",
            ],
            "headers": {
                "X-Version": "338f2fc1036f847626d0905c1f4fbe6d6d287a476c655788b3f1f27b1a78dab2"
            },
            "params": [
                "prompt",
                "max_tokens",
                "temperature",
                "top_p",
                "top_k",
                "frequency_penalty",
                "presence_penalty",
                "repeat_penalty",
            ],
            "url_path": "predictions",
        },
        # llama-3-70b (https://replicate.com/meta/meta-llama-3-70b)
        "llama-3-70b": {
            "model_name": [
                "meta/llama-3-70b",
                "llama-3-70b",
            ],
            "headers": {
                "X-Version": "83c5bdea9941e83be68480bd06ad792f3f295612a24e4678baed34083083a87f"
            },
            "params": [
                "prompt",
                "max_tokens",
                "min_tokens",
                "temperature",
                "top_p",
                "top_k",
                "frequency_penalty",
                "presence_penalty",
            ],
            "url_path": "predictions",
        },
        # llama-3-70b-instruct (https://replicate.com/meta/meta-llama-3-70b-instruct)
        "llama-3-70b-instruct": {
            "model_name": [
                "meta/llama-3-70b-instruct",
                "llama-3-70b-instruct",
            ],
            "headers": {
                "X-Version": "fbfb20b472b2f3bdd101412a9f70a0ed4fc0ced78a77ff00970ee7a2383c575d"
            },
            "params": [
                "prompt",
                "max_tokens",
                "min_tokens",
                "temperature",
                "top_p",
                "top_k",
                "frequency_penalty",
                "presence_penalty",
            ],
            "url_path": "predictions",
        },
        # llama-3-8b (https://replicate.com/meta/meta-llama-3-8b)
        "llama-3-8b": {
            "model_name": [
                "meta/llama-3-8b",
                "llama-3-8b",
            ],
            "headers": {
                "X-Version": "9a9e68fc8695f5847ce944a5cecf9967fd7c64d0fb8c8af1d5bdcc71f03c5e47"
            },
            "params": [
                "prompt",
                "max_tokens",
                "min_tokens",
                "temperature",
                "top_p",
                "top_k",
                "frequency_penalty",
                "presence_penalty",
            ],
            "url_path": "predictions",
        },
        # llama-3-8b-instruct (https://replicate.com/meta/meta-llama-3-8b-instruct)
        "llama-3-8b-instruct": {
            "model_name": [
                "meta/llama-3-8b-instruct",
                "llama-3-8b-instruct",
            ],
            "headers": {
                "X-Version": "5a6809ca6288247d06daf6365557e5e429063f32a21146b2a807c682652136b8"
            },
            "params": [
                "prompt",
                "max_tokens",
                "min_tokens",
                "temperature",
                "top_p",
                "top_k",
                "frequency_penalty",
                "presence_penalty",
            ],
            "url_path": "predictions",
        },
        # wizardcoder-34b-v1.0 (https://replicate.com/rhamnett/wizardcoder-34b-v1.0)
        "wizardcoder-34b-v1.0": {
            "model_name": ["rhamnett/wizardcoder-34b-v1.0", "wizardcoder-34b-v1.0"],
            "headers": {
                "X-Version": "bae902bd8a4032fcf2295523b38da90aae7cc8ca2260e7ca9b8434a981d32278"
            },
            "params": [
                "prompt",
                "n",
                "max_length",
                "temperature",
                "top_p",
                "repetition_penalty",
            ],
            "url_path": "predictions",
        },
        # Stable Diffusion 2.1 (https://replicate.com/stability-ai/stable-diffusion)
        "stable-diffusion": {
            "model_name": [
                "stability-ai/stable-diffusion-2-1",
                "stability-ai/stable-diffusion",
                "stable-diffusion",
            ],
            "headers": {
                "X-Version": "ac732df83cea7fff18b8472768c88ad041fa750ff7682a21affe81863cbe77e4"
            },
            "params": [
                "prompt",
                "height",
                "width",
                "negative_prompt",
                "num_outputs",
                "num_inference_steps",
                "guidance_scale",
                "scheduler",
                "seed",
            ],
            "url_path": "predictions",
        },
        # stability-ai/sdxl (https://replicate.com/stability-ai/sdxl)
        "sdxl": {
            "model_name": [
                "stability-ai/sdxl",
                "sdxl",
            ],
            "headers": {
                "X-Version": "39ed52f2a78e934b3ba6e2a89f5b1c712de7dfea535525255b1aa35c5565e08b"
            },
            "params": [
                "prompt",
                "negative_prompt",
                "image",
                "mask",
                "width",
                "height",
                "num_outputs",
                "scheduler",
                "num_inference_steps",
                "guidance_scale",
                "prompt_strength",
                "seed",
                "refine",
                "high_noise_frac",
                "refine_steps",
                "apply_watermark",
                "lora_scale",
                "disable_safety_checker",
            ],
            "url_path": "predictions",
        },
        # andreasjansson/stable-diffusion-inpainting
        #  (https://replicate.com/andreasjansson/stable-diffusion-inpainting)
        "stable-diffusion-inpainting": {
            "model_name": [
                "andreasjansson/stable-diffusion-inpainting",
                "stable-diffusion-inpainting",
            ],
            "headers": {
                "X-Version": "e490d072a34a94a11e9711ed5a6ba621c3fab884eda1665d9d3a282d65a21180"
            },
            "params": [
                "prompt",
                "negative_prompt",
                "image",
                "mask",
                "invert_mask",
                "num_outputs",
                "num_inference_steps",
                "guidance_scale",
                "seed",
            ],
            "url_path": "predictions",
        },
        # openai/whisper (https://replicate.com/openai/whisper)
        "whisper": {
            "model_name": [
                "openai/whisper",
                "whisper",
            ],
            "headers": {
                "X-Version": "4d50797290df275329f202e48c76360b3f22b08d28c196cbc54600319435f8d2"
            },
            "params": [
                "audio",
                "transcription",
                "translate",
                "language",
                "temperature",
                "patience",
                "suppress_tokens",
                "initial_prompt",
                "condition_on_previous_text",
                "temperature_increment_on_fallback",
                "compression_ratio_threshold",
                "logprob_threshold",
                "no_speech_threshold",
            ],
            "url_path": "predictions",
        },
        # adirik/styletts2 (https://replicate.com/adirik/styletts2)
        "styletts2": {
            "model_name": ["adirik/styletts2", "styletts2"],
            "headers": {
                "X-Version": "989cb5ea6d2401314eb30685740cb9f6fd1c9001b8940659b406f952837ab5ac"
            },
            "params": [
                "weights",
                "text",
                "reference",
                "alpha",
                "beta",
                "diffusion_steps",
                "embedding_scale",
                "seed",
            ],
            "url_path": "predictions",
        },
        # ResNet-50 (https://replicate.com/replicate/resnet)
        "resnet": {
            "model_name": [
                "replicate/resnet",
                "resnet",
                "resnet-50",
            ],
            "headers": {
                "X-Version": "dd782a3d531b61af491d1026434392e8afb40bfb53b8af35f727e80661489767"
            },
            "params": [
                "image",
            ],
            "url_path": "predictions",
        },
        # "HuggingFaceH4/zephyr-7b-beta"
        "huggingfaceh4/zephyr-7b-beta": {
            "model_name": [
                "huggingfaceh4/zephyr-7b-beta",
            ],
            "headers": {"X-Model": "HuggingFaceH4/zephyr-7b-beta"},
            "params": ["inputs", "parameters"],
            "url_path": "seaplane-predictions",
            "default_iata": "sjc",
        },
        # Azure OpenAI GPT35
        "chat-azure-openai-gpt35-turbo16k": {
            "model_name": [
                "chat-azure-openai-gpt35-turbo16k",
                "azure-openai-gpt35-turbo16k",
            ],
            "headers": {"x-auth-sub": "tnt-default"},
            "params": ["messages"],
            "url_path": "chat-azure-openai-gpt35-turbo16k",
        },
        # Azure OpenAI GPT4
        "chat-azure-openai-gpt4": {
            "model_name": [
                "chat-azure-openai-gpt4",
                "azure-openai-gpt4",
            ],
            "headers": {"x-auth-sub": "tnt-default"},
            "params": ["messages"],
            "url_path": "chat-azure-openai-gpt4",
        },
        # Gemini 1.0 Pro
        #  (https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/gemini#request_body)
        "predictions-google-gemini10-pro": {
            "model_name": [
                "predictions-google-gemini10-pro",
                "google-gemini10-pro",
                "gemini10-pro",
            ],
            "headers": {"x-auth-sub": "tnt-default"},
            "params": ["contents", "safety_settings", "generation_config"],
            "url_path": "predictions-google-gemini10-pro",
        },
        # Gemini 1.0 Pro Vision
        #  (https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/gemini#request_body)
        "predictions-google-gemini10-pro-vision": {
            "model_name": [
                "predictions-google-gemini10-pro-vision",
                "google-gemini10-pro-vision",
                "gemini10-pro-vision",
            ],
            "headers": {"x-auth-sub": "tnt-default"},
            "params": ["contents", "safety_settings", "generation_config"],
            "url_path": "predictions-google-gemini10-pro-vision",
        },
        # AWS AI21 Jurassic2 Ultra
        #  (https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-jurassic2.html)
        "predictions-aws-ai21-jurassic2-ultra": {
            "model_name": [
                "predictions-aws-ai21-jurassic2-ultra",
                "aws-ai21-jurassic2-ultra",
                "ai21-jurassic2-ultra",
            ],
            "headers": {"x-auth-sub": "tnt-default"},
            "params": [
                "prompt",
                "temperature",
                "topP",
                "maxTokens",
                "stopSequences",
                "countPenalty",
                "presencePenalty",
                "frequencyPenalty",
            ],
            "url_path": "predictions-aws-ai21-jurassic2-ultra",
        },
        # AWS Anthropic Claude v2.1
        #  (https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-anthropic-claude-messages.html)
        "predictions-aws-anthropic-claude21": {
            "model_name": [
                "predictions-aws-anthropic-claude21",
                "aws-anthropic-claude21",
                "anthropic-claude21",
            ],
            "headers": {"x-auth-sub": "tnt-default"},
            "params": [
                "anthropic_version",
                "max_tokens",
                "system",
                "messages",
                "temperature",
                "top_p",
                "top_k",
                "stop_sequences",
            ],
            "url_path": "predictions-aws-anthropic-claude21",
        },
        # AWS Anthropic Claude Instant v1.2
        #  (https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-anthropic-claude-messages.html)
        "predictions-aws-anthropic-claude-instant12": {
            "model_name": [
                "predictions-aws-anthropic-claude-instant12",
                "aws-anthropic-claude-instant12",
                "anthropic-claude-instant12",
            ],
            "headers": {"x-auth-sub": "tnt-default"},
            "params": [
                "anthropic_version",
                "max_tokens",
                "system",
                "messages",
                "temperature",
                "top_p",
                "top_k",
                "stop_sequences",
            ],
            "url_path": "predictions-aws-anthropic-claude-instant12",
        },
        # AWS Anthropic Claude3-Haiku-20240307
        #  (https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-anthropic-claude-messages.html)
        "predictions-aws-anthropic-claude3-haiku-20240307": {
            "model_name": [
                "predictions-aws-anthropic-claude3-haiku-20240307",
                "aws-anthropic-claude3-haiku-20240307",
                "anthropic-claude3-haiku-20240307",
            ],
            "headers": {"x-auth-sub": "tnt-default"},
            "params": [
                "anthropic_version",
                "max_tokens",
                "system",
                "messages",
                "temperature",
                "top_p",
                "top_k",
                "stop_sequences",
            ],
            "url_path": "predictions-aws-anthropic-claude3-haiku-20240307",
        },
        # AWS Anthropic Claude3-Sonnet-20240229
        #  (https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-anthropic-claude-messages.html)
        "predictions-aws-anthropic-claude3-sonnet-20240229": {
            "model_name": [
                "predictions-aws-anthropic-claude3-sonnet-20240229",
                "aws-anthropic-claude3-sonnet-20240229",
                "anthropic-claude3-sonnet-20240229",
            ],
            "headers": {"x-auth-sub": "tnt-default"},
            "params": [
                "anthropic_version",
                "max_tokens",
                "system",
                "messages",
                "temperature",
                "top_p",
                "top_k",
                "stop_sequences",
            ],
            "url_path": "predictions-aws-anthropic-claude3-sonnet-20240229",
        },
        # AWS Mistral Mixtral 8x7B Instruct v0.1
        #  (https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-runtime_example_bedrock-runtime_InvokeMixtral8x7B_section.html)
        "predictions-aws-mistral-mixtral-8x7b-instruct01": {
            "model_name": [
                "predictions-aws-mistral-mixtral-8x7b-instruct01",
                "aws-mistral-mixtral-8x7b-instruct01",
            ],
            "headers": {"x-auth-sub": "tnt-default"},
            "params": [
                "prompt",
                "max_tokens",
                "temperature",
            ],
            "url_path": "predictions-aws-mistral-mixtral-8x7b-instruct01",
        },
    }

    def get_model(self, model_name: str) -> str:
        """
        Returns supported model name, or "default" if none matched
        """
        for model in self.models:
            if model_name in self.models[model]["model_name"]:
                return model
        # no model found
        raise SeaplaneError(f"A valid model could not be found matching '{model_name}'.")

    def get_headers(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Returns headers and parameters for the selected model
        """
        headers = {"content-type": "application/json", "X-Division": self.app_division}
        model_name = input_data.get("model", "None").lower()
        if model_name == "replicate-custom":
            headers.update({"X-Version": input_data["replicate-version"]})
            return {
                "headers": headers,
                "data": input_data["replicate-params"],
                "url_path": "predictions",
            }

        model = self.get_model(model_name)

        if self.models[model]["url_path"] == "seaplane-predictions":
            # TODO: If no region provided or configured, default to region where app is deployed
            deploy_region = config.region if config.region else self.models[model]["default_iata"]
            region = input_data.get("region", deploy_region)
            headers.update({"X-Iata": region})
        headers.update(self.models[model]["headers"])
        data = {}

        for param in self.models[model]["params"]:
            value = input_data.get(param)
            if value:
                data[param] = value
            elif param == "anthropic_version":
                data[param] = "bedrock-2023-05-31"

        # TODO: Should also check for specific params like "prompt" that are required
        if data == {}:
            raise SeaplaneError(f"No valid params were found for the selected model {model}.")

        return {
            "headers": headers,
            "data": data,
            "url_path": self.models[model]["url_path"],
        }

    def make_request(self, input_data: Dict[str, Any]) -> Dict[Any, Any]:
        """
        Makes the request to substation and returns the request information, including ID.

        `input_data` (Dict/JSON) must include `"model"` (see below) and at least one input:

          For LLMs, usually `"prompt"` and optional args, like `"temperature"`.

          For embeddings, `"text"` (string) or `"texts"`/`"text_batch"` (JSON list of strings).

        Supported models:
          `"embeddings"` (aka `"all-mpnet-base-v2"`) /
          `"embeddings-ext"` /
          `"zephyr-7b-beta"` /
          `"mistral-7b-instruct-v0.1"` /
          `"mistral-7b-instruct-v0.2"` /
          `"mixtral-8x7b-instruct"` /
          `"bge-large-en-v1.5"` /
          `"starling-lm-7b-alpha"` /
          `"yi-34b-chat"` /
          `"yi-6b"` /
          `"falcon-40b-instruct"` /
          `"vicuna-13b"` /
          `"phi-2"` /
          `"olmo-7b"` /
          `"llama-2-7b-chat"` /
          `"llama-2-13b-chat"` /
          `"llama-2-70b-chat"` (default model) /
          `"codellama-7b-instruct"` /
          `"codellama-7b-python"` /
          `"codellama-13b-instruct"` /
          `"codellama-34b-instruct"` /
          `"codellama-34b-python"` /
          `"codellama-70b"` /
          `"codellama-70b-instruct"` /
          `"codellama-70b-python"` /
          `"llama-3-70b"` /
          `"llama-3-70b-instruct"` /
          `"llama-3-8b"` /
          `"llama-3-8b-instruct"` /
          `"wizardcoder-34b-v1.0"` /
          `"stable-diffusion-2-1"` /
          `"sdxl"` /
          `"stable-diffusion-inpainting"` /
          `"whisper"` /
          `"styletts2"` /
          `"resnet-50"` /
          `"huggingfaceh4/zephyr-7b-beta"` /
          `"chat-azure-openai-gpt35-turbo16k"` /
          `"chat-azure-openai-gpt4"` /
          `"predictions-google-gemini10-pro"` /
          `"predictions-google-gemini10-pro-vision"` /
          `"predictions-aws-ai21-jurassic2-ultra"` /
          `"predictions-aws-anthropic-claude21"` /
          `"predictions-aws-anthropic-claude-instant12"` /
          `"predictions-aws-anthropic-claude3-haiku-20240307"` /
          `"predictions-aws-anthropic-claude3-sonnet-20240229"` /
          `"predictions-aws-mistral-mixtral-8x7b-instruct01"`

        """

        if not input_data.get("model"):
            raise SeaplaneError("No 'model' was specified. Please select a valid model.")

        # get headers and parameters for the specified model
        model_params = self.get_headers(input_data)

        proxy_addr = os.getenv("SEAPLANE_PROXY_ADDRESS", "localhost:4195")
        url = f"http://{proxy_addr}/{model_params['url_path']}"

        resp = requests.post(url, headers=model_params["headers"], json=model_params["data"])
        if resp.status_code != 200:
            raise SeaplaneError("Error making substation request")

        request_data = {"request": resp.json(), "input_data": input_data}
        return request_data

    def get_response(self, msg: Any) -> Generator[Any, None, None]:
        """
        Use this task to get the completed substation response
        """
        data = json.loads(msg.body)

        # If this is request data from the previous task it will have "request"
        #  See if there is matching response data in KV
        if "request" in data:
            request = data["request"]
            response = kv_store.get(self.response_store, request["id"])

            # Some models have sync response...
            if "output" in request:
                ret = msg.result(json.dumps(data).encode())
                yield ret

            # ...but most do not
            elif response:
                response = json.loads(response)

                # clean up output a little
                if type(response["output"]) is list:
                    if type(response["output"][0]) is str:
                        output = "".join(response["output"])
                        response["output"] = output
                        if "https://replicate.delivery" in output:
                            obj = ObjectStorageAPI()
                            output_dl = requests.get(output)
                            obj_name = (
                                f"{msg.meta['_seaplane_request_id']}.{output.split('.')[-1]}"
                            )
                            bucket = f"{self.app_division.lower()}-downloads"
                            if bucket not in obj.list_buckets():
                                print(f"creating bucket {bucket}")
                                obj.create_bucket(bucket)
                            obj.upload(bucket, obj_name, output_dl.content)
                            response["output"] = {"bucket": bucket, "object": obj_name}
                data.update(response)
                for key in (
                    "request",
                    "logs",
                    "urls",
                    "version",
                    "webhook",
                    "webhook_events_filter",
                ):
                    data.pop(key, "")

                seaplane_batch_hierarchy = msg.meta["_seaplane_batch_hierarchy"]
                ret = msg.result(json.dumps(data).encode())
                ret.override_batch_hierarchy(seaplane_batch_hierarchy)
                yield ret
                kv_store.delete_key(self.response_store, request["id"])
            else:
                # store request metadata for later output
                seaplane_meta: Dict[str, Any] = {}
                for key in msg.meta:
                    seaplane_meta.update({key: msg.meta[key]})
                request.update(
                    {
                        "input_data": data["input_data"],
                        "seaplane_meta": seaplane_meta,
                    }
                )
                kv_store.set_key(self.request_store, request["id"], json.dumps(request).encode())
                log.logger.info(
                    "storing request, dropping message, waiting for substation response"
                )
                yield

        # If this is a response from Substation it will have "output"
        #  See if there matching request data in KV
        elif "output" in data:
            request = kv_store.get(self.request_store, data["id"])
            if request:
                request = json.loads(request)

                # restore the original request_id and batch_hierarchy
                seaplane_request_id = request["seaplane_meta"]["_seaplane_request_id"]
                seaplane_batch_hierarchy = request["seaplane_meta"]["_seaplane_batch_hierarchy"]

                # restore user input data
                data.update({"input_data": request["input_data"]})

                # clean up output a little
                if type(data["output"]) is list:  # many LLMs
                    if type(data["output"][0]) is str:  # LLM, not embeddings
                        output = "".join(data["output"])
                        data["output"] = output
                        if "https://replicate.delivery" in output:
                            obj = ObjectStorageAPI()
                            response = requests.get(output)
                            obj_name = f"{seaplane_request_id}.{output.split('.')[-1]}"
                            bucket = f"{self.app_division.lower()}-downloads"
                            if bucket not in obj.list_buckets():
                                print(f"creating bucket {bucket}")
                                obj.create_bucket(bucket)
                            obj.upload(bucket, obj_name, response.content)
                            data["output"] = {"bucket": bucket, "object": obj_name}
                elif type(data["output"]) is str:  # text to speech (url)
                    output = data["output"]
                    if "https://replicate.delivery" in output:
                        obj = ObjectStorageAPI()
                        output_dl = requests.get(output)
                        obj_name = f"{seaplane_request_id}.{output.split('.')[-1]}"
                        bucket = f"{self.app_division.lower()}-downloads"
                        if bucket not in obj.list_buckets():
                            print(f"creating bucket {bucket}")
                            obj.create_bucket(bucket)
                        obj.upload(bucket, obj_name, output_dl.content)
                        data["output"] = {"bucket": bucket, "object": obj_name}
                for key in (
                    "logs",
                    "urls",
                    "version",
                    "webhook",
                    "webhook_events_filter",
                ):
                    data.pop(key, "")

                ret = msg.result(json.dumps(data).encode())
                for key in request["seaplane_meta"]:
                    ret.meta[key] = request["seaplane_meta"][key]
                ret.output_id = seaplane_request_id
                ret.override_batch_hierarchy(seaplane_batch_hierarchy)
                request.pop("seaplane_meta")
                yield ret
                kv_store.delete_key(self.request_store, data["id"])
            else:
                # For now, sync responses are not showing up on the AI results stream,
                # BUT if that changes we will want to skip this.
                kv_store.set_key(self.response_store, data["id"], json.dumps(data).encode())
                yield
