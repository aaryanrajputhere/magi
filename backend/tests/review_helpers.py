"""Read the source and task messages recorded by conflict-review test clients."""
import json


def review_payload(request):
    if isinstance(request.input, str):
        return json.loads(request.input)
    return {key: value for message in request.input
            for key, value in json.loads(message['content']).items()}
