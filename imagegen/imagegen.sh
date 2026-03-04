#!/bin/bash
# imagegen.sh - Generate images using Google Imagen 4 API
# Usage: imagegen.sh "prompt" [output_path] [aspect_ratio]
# Aspect ratios: 1:1, 16:9, 9:16, 3:4, 4:3

PROMPT="$1"
OUTPUT="${2:-$(pwd)/generated-image.png}"
ASPECT="${3:-16:9}"
API_KEY=$(security find-generic-password -s 'gemini-api-key' -w 2>/dev/null)

if [ -z "$PROMPT" ]; then
  echo "Usage: imagegen.sh \"prompt\" [output_path] [aspect_ratio]"
  exit 1
fi

if [ -z "$API_KEY" ]; then
  echo "Error: No Gemini API key found in keychain (key: gemini-api-key)"
  exit 1
fi

curl -s "https://generativelanguage.googleapis.com/v1beta/models/imagen-4.0-fast-generate-001:predict?key=${API_KEY}" \
  -H "Content-Type: application/json" \
  -X POST \
  -d "{
    \"instances\": [{\"prompt\": $(echo "$PROMPT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))')}],
    \"parameters\": {\"sampleCount\": 1, \"aspectRatio\": \"${ASPECT}\"}
  }" | python3 -c "
import json, sys, base64
data = json.load(sys.stdin)
if 'error' in data:
    print(f'Error: {data[\"error\"][\"message\"]}', file=sys.stderr)
    sys.exit(1)
if 'predictions' in data:
    img = data['predictions'][0]['bytesBase64Encoded']
    with open('${OUTPUT}', 'wb') as f:
        f.write(base64.b64decode(img))
    print(f'Saved to ${OUTPUT}')
else:
    print(f'Unexpected response: {json.dumps(data)[:300]}', file=sys.stderr)
    sys.exit(1)
"
