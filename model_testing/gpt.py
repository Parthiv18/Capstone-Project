import google.generativeai as genai

# Configure API key
genai.configure(api_key="")

# Select model
model = genai.GenerativeModel("gemini-flash-latest")

# Full structured prompt
prompt = """
Using a smart HVAC AI logic, generate only a Markdown table for a 24-hour timeline.

House Variables:
- Size: 1000 sq ft
- Age: 1 year
- Insulation: Excellent
- HVAC: Central (2 years old)
- Personal Comfort: 25°C
- Appliances: Gas Water Heater

Environmental Data:
- Location: Brampton (Dec 17)
- Outdoor Temp: 2°C
- Apparent Temp: -1°C
- Wind: 5.8 m/s
- Electricity Pricing: Ontario Time-of-Use
  - On-peak: 7–11 AM, 5–7 PM
  - Mid-peak: 11 AM–5 PM
  - Off-peak: 7 PM–7 AM

Requirements:
- Output ONLY a Markdown table
- Columns must be exactly:
  Time Range | HVAC Status MODE | Power (kWh) | Reasoning
- Apply the Pre-Heat heuristic to shift load away from On-peak hours
- Use the OFF condition when predicted temperature (Tpred) stays within the comfort band (Tset ± δ) during expensive pricing windows
- Do NOT include explanations outside the table
"""

# Generate response
response = model.generate_content(prompt)

# Print only the model output
print(response.text)
