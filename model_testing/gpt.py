import google.generativeai as genai

# Set your API key
genai.configure(api_key="")

# Pick a model (Gemini 1.5 Flash is fast & cheap)
model = genai.GenerativeModel("gemini-flash-latest")

# Send a prompt
prompt = "What is HVAC."

response = model.generate_content(prompt)

# Print the response text
print(response.text)
