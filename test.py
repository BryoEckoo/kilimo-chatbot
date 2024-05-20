import json
msg = [{'head':'sam','body':'cat'}]

data = json.loads(msg.body)

output = {}

output["model"] = "embeddings"
output["prompt"] = data["query"]
output["chat_history"] = data["chat_history"]

print(output)