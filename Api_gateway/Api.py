{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "uoa": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "UoA Agentic Gateway",
      "options": {
        "baseURL": "https://agent.elliottwen.info/v1",
        "apiKey": ""## put your API key here
      },
      "models": {
        "MiniMax-M3": { "name": "MiniMax-M3" }
      }
    }
  },
  "model": "uoa/MiniMax-M3"
}