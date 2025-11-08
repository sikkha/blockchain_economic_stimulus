import os
import json
import requests
from configparser import ConfigParser
#from openai import OpenAI  # Import OpenAI if you're using GPT directly.

class APIKeyManager:
    _keys = None

    @classmethod
    def load_keys(cls):
        if cls._keys is None:
            config = ConfigParser()
            config_file_directory = os.getenv("API_KEY_FILE_PATH", ".")
            config_file_name = "api_key.conf"
            config_file_path = os.path.join(config_file_directory, config_file_name)
            config.read(config_file_path)
            cls._keys = {
                'palm2': config.get('API_KEYS', 'GOOGLE_API_KEY', fallback=None),
                'gemini-pro': config.get('API_KEYS', 'GOOGLE_API_KEY', fallback=None),
                'mistral': config.get('API_KEYS', 'MISTRAL_API_KEY', fallback=None),
                'openai': config.get('API_KEYS', 'OPENAI_API_KEY', fallback=None),
                'anthropic': config.get('API_KEYS', 'ANTHROPIC_API_KEY', fallback=None),
                'groq': config.get('API_KEYS', 'GROQ_API_KEY', fallback=None),  # Added Groq API key
                'openrouter': config.get('API_KEYS', 'OPENROUTER_API_KEY', fallback=None),
                'ollama': config.get('API_KEYS', 'OLLAMA_API_KEY', fallback=None),
                'xai': config.get('API_KEYS', 'XAI_API_KEY', fallback=None),
                'my_llm': "mocked_api_key"  # Mock API key for my_llm
            }
        return cls._keys

def call_LLM(model, prompt):
    api_keys = APIKeyManager.load_keys()
    api_key = api_keys.get(model)
    
    # Confirm api_key is present
    if not api_key:
        return f"Error: API key for model '{model}' is missing."

    headers = {'Content-Type': 'application/json'}
    if model in ['openai', 'mistral', 'anthropic', 'openrouter', 'xai']:  # Add xai to Bearer tokens
        headers['Authorization'] = f'Bearer {api_key}'

    url, data = None, None

    # Define URLs and data for each model
    if model == 'palm2':
        url = f"https://generativelanguage.googleapis.com/v1beta3/models/text-bison-001:generateText?key={api_key}"
        data = {"prompt": {"text": prompt}}
    elif model == 'gemini-pro':
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={api_key}"
        data = {"contents": [{"parts": [{"text": prompt}]}]}
    elif model == 'mistral':
        url = "https://api.mistral.ai/v1/chat/completions"
        data = {"model": "mistral-medium", "messages": [{"role": "user", "content": prompt}]}
    elif model == 'openai':
        url = "https://api.openai.com/v1/chat/completions"
        data = {
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ]
        }

    elif model == 'ollama':
    # New Ollama integration
        url = "http://localhost:11434/api/generate"
        data = {
            "model": "gpt-oss:120b-cloud",
            "prompt": prompt,
            "stream": False
        }

    elif model == 'anthropic':
        url = "https://api.anthropic.com/v1/messages"
        data = {
            "model": "claude-3-opus-20240229",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}]
        }
    elif model == 'groq':
        url = "https://api.groq.com/openai/v1/chat/completions"
        data = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}]
        }

        # Add browser-like headers
        headers = {
            "Authorization": f"Bearer {api_key}",  # Ensure API key is correctly passed
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",  # Simulate a browser
            "Referer": "https://your-app-domain.com",  # Optional: Replace with your domain if needed
            "Origin": "https://your-app-domain.com"    # Optional: If required by CORS
        }

        # Make the API request
        try:
            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()  # Raise an error for bad responses
            return extract_text_from_response(model, response.json())
        except requests.exceptions.HTTPError as http_err:
            return f"HTTP error occurred: {http_err} - {response.text}"
        except Exception as err:
            return f"An error occurred: {err}"

    elif model == 'openrouter':
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers.update({
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "YOUR_SITE_URL",  # Optional for rankings on openrouter.ai
            "X-Title": "YOUR_APP_NAME",  # Optional for rankings
        })
        data = {
            "model": "meta-llama/llama-3.1-405b-instruct",
            "messages": [{"role": "user", "content": prompt}]
        }
    elif model == 'xai':
        # Set up for x.ai model
        url = "https://api.x.ai/v1/chat/completions"
        data = {
            "model": "grok-beta",
            "stream": False,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "You are a female assistant, named 'AI Blue'."},
                {"role": "user", "content": prompt}
            ]
        }

    elif model == 'my_llm':
    # Set up for my custom LLM (Ollama or any other local model)
        url = "http://localhost:11434/api/generate"
        data = {
            "model": "llama3",  # Adjust as needed
            "stream": False,
            "prompt": prompt
        }

    # Confirm URL and data are correctly set
    if not url or not data:
        return "Error: Model or parameters not correctly specified."

    # Execute request
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()  # Will raise an HTTPError for bad responses
        return extract_text_from_response(model, response.json())
    except requests.exceptions.HTTPError as http_err:
        return f"HTTP error occurred: {http_err} - {response.text}"
    except Exception as err:
        return f"An error occurred: {err}"



def extract_text_from_response(model, response):
    """Extract text content from the LLM response based on the model."""
    if model == 'palm2':
        # Assuming Google API response format for palm2 is {'candidates': [{'output': '...'}]}
        return response.get('candidates', [{}])[0].get('output', '')
    elif model == 'gemini-pro':
        # Assuming Google API response format for gemini-pro is {'candidates': [{'content': {'parts': [{'text': '...'}]}}]}
        return response.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
    elif model == 'mistral':
        # Assuming Mistral API response format is {'choices': [{'message': {'content': '...'}}]}
        return response.get('choices', [{}])[0].get('message', {}).get('content', '')
    elif model == 'openai':
        # Assuming OpenAI API response format includes {'choices': [{'message': {'content': '...'}}]}
        return response.get('choices', [{}])[0].get('message', {}).get('content', '')

    elif model == 'ollama':
        # Ollama (local model API)
        # Example response:
        # {
        #   "model":"gpt-oss:120b-cloud",
        #   "response":"The sky looks blue because ...",
        #   ...
        # }
        return response.get('response', '')

    elif model == 'anthropic':
        # Assuming Anthropic API response format includes {'completion': '...'}
        return response.get('completion', '')
    elif model == 'groq' or model == 'openrouter':
        # Assuming Groq and Openrouter API response format similar to OpenAI
        return response.get('choices', [{}])[0].get('message', {}).get('content', '')
    elif model == 'xai':
        # Assuming x.ai response format similar to OpenAI: {'choices': [{'message': {'content': '...'}}]}
        return response.get('choices', [{}])[0].get('message', {}).get('content', '')
    elif model == 'my_llm':
        # Assuming Ollama API response format includes {'response': '...'}
        return response.get('response', '')


    return "Unsupported model or incorrect response format"
