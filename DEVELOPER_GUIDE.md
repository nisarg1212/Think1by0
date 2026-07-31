# Think1by0: Developer Guide & Tutorial

Welcome! This guide is designed to teach you the concepts behind the **Think1by0** project, explain how Django projects work, and walk you through how to implement each part of the codebase step-by-step.

---

## Table of Contents
1. [Core Concepts: Django and Django Apps](#1-core-concepts-django-and-django-apps)
2. [Setting Up Your Local Environment](#2-setting-up-your-local-environment)
3. [Understanding the Architecture](#3-understanding-the-architecture)
4. [Step-by-Step Implementation Guide](#4-step-by-step-implementation-guide)
   - [Step 4.1: Implementing the Agents](#step-41-implementing-the-agents)
   - [Step 4.2: Implementing the Services](#step-42-implementing-the-services)
   - [Step 4.3: Exposing the API Views](#step-43-exposing-the-api-views)
5. [Git and Committing Your Work](#5-git-and-committing-your-work)

---

## 1. Core Concepts: Django and Django Apps

Django is a high-level Python web framework that encourages rapid development and clean, pragmatic design. It is structured around the **MVT (Model-View-Template)** pattern, but since we are building a backend API, we will focus on **Views** that return JSON responses rather than rendering HTML templates.

*   **Django Project (`think1by0_django_folder`):** The configuration root. It contains:
    *   `settings.py`: Registers installed apps, database backends, and secret settings.
    *   `urls.py`: The entrypoint for routing URLs to specific apps.
*   **Django App (`apis`):** A self-contained web application inside the project that performs a specific function (in our case, exposing the agent routing services).

---

## 2. Setting Up Your Local Environment

### 2.1. The Virtual Environment (`.venv`)
A virtual environment is an isolated environment where python packages are installed. This prevents version conflicts between projects.
To activate your virtual environment on Windows (PowerShell):
```powershell
.\Backend\.venv\Scripts\Activate.ps1
```

### 2.2. Dependencies
To implement our LLM agents, we'll need to install dependencies. Activating the virtual environment first ensures they are installed locally:
```powershell
pip install django djangorestframework python-dotenv google-genai openai requests
```
*   `djangorestframework`: Helps in writing clean RESTful APIs.
*   `python-dotenv`: Loads API keys from a `.env` file.
*   `google-genai`: The official SDK to interact with Google Gemini models.
*   `openai`: Used to interact with NVIDIA NIMs and OpenRouter, as both support the standard OpenAI interface format.

### 2.3. Local Configuration (`.env`)
Create a file named `.env` in the project root:
```env
GEMINI_API_KEY=your_key_here
NVIDIA_API_KEY=your_key_here
OPENROUTER_API_KEY=your_key_here
```

---

## 3. Understanding the Architecture

Think1by0 is designed as a **mixture-of-agents router**. Here is how the components communicate:

1.  **Client Request:** A front-end client posts a prompt to your Django API.
2.  **Router:** Decides which model handles the prompt. For example:
    *   *Simple tasks* (e.g., "Summarize this text") -> Routed to Gemini Flash (low cost, fast).
    *   *Complex programming/reasoning* -> Routed to Claude 3.5 Sonnet (via OpenRouter).
3.  **Orchestrator:** Manages the task flow. If a task requires multiple steps, the orchestrator calls multiple agents sequentially.
4.  **Judge:** Verifies the quality and formatting of the output. If the response fails, it can re-query the agent with feedback.

---

## 4. Step-by-Step Implementation Guide

### Step 4.1: Implementing the Agents

#### Gemini Agent
File: `Backend/apis/agents/gemini_agent.py`
To communicate with Gemini, use the `google-genai` library:
```python
import os
from google import genai
from google.genai import types

class GeminiAgent:
    def __init__(self):
        # Retrieve the key from the environment variables
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.client = genai.Client(api_key=self.api_key)

    def query(self, prompt: str, system_instruction: str = None) -> str:
        config = types.GenerateContentConfig()
        if system_instruction:
            config.system_instruction = system_instruction
            
        # Call the gemini-2.5-flash model
        response = self.client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=config
        )
        return response.text
```

#### OpenRouter Agent
File: `Backend/apis/agents/openrouter_agent.py`
OpenRouter implements the standard OpenAI interface, allowing us to use the `openai` SDK:
```python
import os
from openai import OpenAI

class OpenRouterAgent:
    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self.api_key,
        )

    def query(self, prompt: str, model: str = "anthropic/claude-3.5-sonnet") -> str:
        completion = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        return completion.choices[0].message.content
```

---

### Step 4.2: Implementing the Services

#### Router
File: `Backend/apis/services/router.py`
The router decides which agent fits the user input. In its simplest form, you can parse keywords, or query a fast LLM to classify the task:
```python
class LLMRouter:
    def route_request(self, prompt: str) -> str:
        # Simplistic keyword-based routing for demonstration:
        prompt_lower = prompt.lower()
        if "write code" in prompt_lower or "debug" in prompt_lower:
            return "openrouter"  # Route coding tasks to Claude via OpenRouter
        else:
            return "gemini"      # Default simple tasks to Gemini Flash
```

#### Orchestrator
File: `Backend/apis/services/orchestrator.py`
The orchestrator triggers the agent suggested by the router:
```python
from apis.agents.gemini_agent import GeminiAgent
from apis.agents.openrouter_agent import OpenRouterAgent

class AgentOrchestrator:
    def __init__(self):
        self.gemini = GeminiAgent()
        self.openrouter = OpenRouterAgent()

    def execute(self, prompt: str, agent_name: str) -> str:
        if agent_name == "openrouter":
            return self.openrouter.query(prompt)
        else:
            return self.gemini.query(prompt)
```

---

### Step 4.3: Exposing the API Views

Create a view in `Backend/apis/views.py` that processes incoming JSON:
```python
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from apis.services.router import LLMRouter
from apis.services.orchestrator import AgentOrchestrator

class ChatView(APIView):
    def post(self, request):
        prompt = request.data.get("prompt")
        if not prompt:
            return Response({"error": "Prompt is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # 1. Route the request
            router = LLMRouter()
            target_agent = router.route_request(prompt)
            
            # 2. Execute agent response
            orchestrator = AgentOrchestrator()
            result = orchestrator.execute(prompt, target_agent)
            
            return Response({
                "routed_to": target_agent,
                "response": result
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
```

Register this view in `Backend/apis/urls.py` and configure `Backend/think1by0_django_folder/urls.py` to route requests to it.

---

## 5. Git and Committing Your Work

Since you have updated `.gitignore`, you are safe from committing virtual environments, databases, and api keys!

To commit your current workspace setup:
1. Check the git status to verify what is tracked:
   ```bash
   git status
   ```
2. Add files to the staging area:
   ```bash
   git add .
   ```
3. Commit your changes:
   ```bash
   git commit -m "chore: setup .gitignore, developer guide, and AI instructions"
   ```
4. Push to your repository (if remote is set up):
   ```bash
   git push origin main
   ```