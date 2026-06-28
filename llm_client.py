import os
import sys
import shutil
import requests
from dotenv import load_dotenv
from rich.console import Console

# Initialize rich console
console = Console()
err_console = Console(stderr=True)


def resolve_env_path() -> str:
    """Resolves the location of the .env file holding API keys / config.

    Resolution order:
      1. ~/.psyche/.env (preferred — survives npm updates, lives outside the
         installed package directory).
      2. Legacy package-dir .env (back-compat). If found there while
         ~/.psyche/.env does not exist, it is migrated once to ~/.psyche/.env.

    Returns the path that should be used for reads and writes (~/.psyche/.env).
    """
    psyche_dir = os.path.expanduser("~/.psyche")
    primary_path = os.path.join(psyche_dir, ".env")
    legacy_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

    if not os.path.exists(primary_path) and os.path.exists(legacy_path):
        # One-time migration of an existing user's keys.
        try:
            os.makedirs(psyche_dir, exist_ok=True)
            shutil.copy2(legacy_path, primary_path)
            print("Migrated .env to ~/.psyche/.env")
        except Exception:
            # If the copy fails, fall back to using the legacy path directly.
            return legacy_path

    return primary_path


# Load environment variables (prefer ~/.psyche/.env, migrating legacy if needed)
_env_path = resolve_env_path()
if os.path.exists(_env_path):
    load_dotenv(_env_path)
else:
    load_dotenv()

def check_and_run_setup():
    """Checks if .env is missing or unconfigured and runs an interactive setup wizard if needed."""
    # Prevent blocking during unit testing or non-interactive environments
    if "unittest" in sys.modules or os.getenv("TESTING") == "true" or os.getenv("PSYCHE_NONINTERACTIVE") == "1" or not sys.stdin.isatty():
        return
        
    env_path = resolve_env_path()
    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)
        provider = os.getenv("LLM_PROVIDER")
        if provider in ["gemini", "openai"] and os.getenv(f"{provider.upper()}_API_KEY"):
            return
        elif provider in ["ollama", "none", "local"]:
            return
            
    run_setup_wizard(env_path)

def run_setup_wizard(env_path: str):
    from rich.prompt import Prompt
    
    console.print("\n[bold green]🛠️ Welcome to the Knowledge CLI Setup Wizard[/bold green]")
    console.print("Let's configure your local environment variables. This will create a `.env` file.\n")
    
    # Prompt for LLM Provider
    console.print("[bold cyan]Choose your LLM / Embedding Provider:[/bold cyan]")
    console.print("  [bold]4[/bold]) Local / Offline (Recommended - 100% on-device ONNX embeddings, no keys, nothing leaves your machine)")
    console.print("  [bold]3[/bold]) Ollama (100% Offline, Local & Free - adds a local chat model; requires the Ollama service running)")
    console.print("  [bold]1[/bold]) Gemini API (Cloud - sends text to Google; requires a Gemini API Key)")
    console.print("  [bold]2[/bold]) OpenAI API (Cloud - sends text to OpenAI; requires an OpenAI API Key)")
    console.print("  [bold]5[/bold]) AI-Free / Pure Retrieval (Offline-only, no embeddings, FTS5 keyword search)")

    choice = Prompt.ask("Select option", choices=["1", "2", "3", "4", "5"], default="4")
    
    provider = "gemini"
    api_key = ""
    ollama_host = "http://localhost:11434"
    embed_model = ""
    chat_model = ""
    
    if choice == "1":
        provider = "gemini"
        api_key = Prompt.ask("Enter your Gemini API Key", password=True)
        embed_model = "text-embedding-004"
        chat_model = "gemini-1.5-flash"
    elif choice == "2":
        provider = "openai"
        api_key = Prompt.ask("Enter your OpenAI API Key", password=True)
        embed_model = "text-embedding-3-small"
        chat_model = "gpt-4o-mini"
    elif choice == "3":
        provider = "ollama"
        ollama_host = Prompt.ask("Enter Ollama Host Address", default="http://localhost:11434")
        embed_model = Prompt.ask("Enter embedding model name", default="nomic-embed-text")
        chat_model = Prompt.ask("Enter chat model name", default="llama3")
    elif choice == "4":
        provider = "local"
        embed_model = "BAAI/bge-small-en-v1.5"
        chat_model = "none"
    elif choice == "5":
        provider = "none"
        embed_model = "none"
        chat_model = "none"

    # For local/none providers, optionally pair a chat model
    chat_provider = ""
    chat_provider_model = ""
    chat_provider_key = ""
    if choice in ("4", "5"):
        cp = Prompt.ask(
            "Pair a chat model for guidance/check-in? (ollama/gemini/openai/none)",
            default="none"
        ).lower()
        if cp in ("ollama", "gemini", "openai"):
            chat_provider = cp
            if cp == "ollama":
                chat_provider_model = Prompt.ask("Enter Ollama chat model name", default="llama3")
            elif cp == "openai":
                chat_provider_model = Prompt.ask("Enter OpenAI chat model name", default="gpt-4o-mini")
                chat_provider_key = Prompt.ask("Enter your OpenAI API Key", password=True)
            elif cp == "gemini":
                chat_provider_model = Prompt.ask("Enter Gemini chat model name", default="gemini-1.5-flash")
                chat_provider_key = Prompt.ask("Enter your Gemini API Key", password=True)

    # Write .env file
    try:
        env_dir = os.path.dirname(env_path)
        if env_dir:
            os.makedirs(env_dir, exist_ok=True)
        with open(env_path, "w") as f:
            f.write(f"# Configured via Setup Wizard\n")
            f.write(f"LLM_PROVIDER={provider}\n")
            f.write(f"DATABASE_PATH=knowledge.db\n")
            if provider == "gemini":
                f.write(f"GEMINI_API_KEY={api_key}\n")
                f.write(f"EMBED_MODEL={embed_model}\n")
                f.write(f"CHAT_MODEL={chat_model}\n")
            elif provider == "openai":
                f.write(f"OPENAI_API_KEY={api_key}\n")
                f.write(f"EMBED_MODEL={embed_model}\n")
                f.write(f"CHAT_MODEL={chat_model}\n")
            elif provider == "ollama":
                f.write(f"OLLAMA_HOST={ollama_host}\n")
                f.write(f"EMBED_MODEL={embed_model}\n")
                f.write(f"CHAT_MODEL={chat_model}\n")
            elif provider == "local":
                f.write(f"EMBED_MODEL={embed_model}\n")
                f.write(f"CHAT_MODEL={chat_model}\n")
                if chat_provider:
                    f.write(f"CHAT_PROVIDER={chat_provider}\n")
                    f.write(f"CHAT_MODEL={chat_provider_model}\n")
                    if chat_provider_key:
                        if chat_provider == "openai":
                            f.write(f"OPENAI_API_KEY={chat_provider_key}\n")
                        elif chat_provider == "gemini":
                            f.write(f"GEMINI_API_KEY={chat_provider_key}\n")
            elif provider == "none":
                f.write(f"EMBED_MODEL={embed_model}\n")
                f.write(f"CHAT_MODEL={chat_model}\n")
                if chat_provider:
                    f.write(f"CHAT_PROVIDER={chat_provider}\n")
                    f.write(f"CHAT_MODEL={chat_provider_model}\n")
                    if chat_provider_key:
                        if chat_provider == "openai":
                            f.write(f"OPENAI_API_KEY={chat_provider_key}\n")
                        elif chat_provider == "gemini":
                            f.write(f"GEMINI_API_KEY={chat_provider_key}\n")
                
        console.print(f"\n✨ [bold green]Configuration saved successfully to {env_path}![/bold green]\n")
        # Reload environment variables
        load_dotenv(env_path, override=True)
        
        # Pre-download local embedding model if chosen, to prevent first-query MCP timeouts
        if provider == "local":
            console.print("[cyan]Pre-downloading local ONNX embedding model (BAAI/bge-small-en-v1.5) to prevent MCP timeouts...[/cyan]")
            try:
                from fastembed import TextEmbedding
                TextEmbedding(model_name=embed_model)
                console.print("[green]✓ Model downloaded and cached successfully.[/green]\n")
            except Exception as download_err:
                console.print(f"[yellow]Warning: Could not pre-download model: {download_err}. It will download on first query.[/yellow]\n")
    except Exception as e:
        err_console.print(f"[bold red]Error saving configuration file:[/bold red] {e}")
        sys.exit(1)

class LLMClient:
    def __init__(self):
        check_and_run_setup()
        
        self.provider = os.getenv("LLM_PROVIDER", "local").lower()
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self._local_model = None
        
        # Configure models
        if self.provider == "openai":
            self.embed_model = os.getenv("EMBED_MODEL") or "text-embedding-3-small"
            self.chat_model = os.getenv("CHAT_MODEL") or "gpt-4o-mini"
        elif self.provider == "ollama":
            self.embed_model = os.getenv("EMBED_MODEL") or "nomic-embed-text"
            self.chat_model = os.getenv("CHAT_MODEL") or "llama3"
        elif self.provider == "local":
            self.embed_model = os.getenv("EMBED_MODEL") or "BAAI/bge-small-en-v1.5"
            self.chat_model = "none"
        elif self.provider in ("none", "offline"):
            self.provider = "none"
            self.embed_model = "none"
            self.chat_model = "none"
        else: # Default is gemini
            self.provider = "gemini"
            self.embed_model = os.getenv("EMBED_MODEL") or "text-embedding-004"
            self.chat_model = os.getenv("CHAT_MODEL") or "gemini-1.5-flash"
            
        # Verify credentials for API-based keys
        if self.provider == "openai" and not self.openai_key:
            raise ValueError("LLM_PROVIDER is set to 'openai' but OPENAI_API_KEY is not configured in .env.")
        elif self.provider == "gemini" and not self.gemini_key:
            raise ValueError("LLM_PROVIDER is set to 'gemini' but GEMINI_API_KEY is not configured in .env.")

        # Chat provider — may differ from embedding provider
        chat_provider = os.getenv("CHAT_PROVIDER", "").lower() or self.provider
        self.chat_provider = chat_provider
        if chat_provider in ("none", "local", "offline"):
            self.chat_model = "none"
        elif chat_provider == "ollama":
            self.chat_model = os.getenv("CHAT_MODEL") or "llama3"
        elif chat_provider == "openai":
            self.chat_model = os.getenv("CHAT_MODEL") or "gpt-4o-mini"
            if not self.openai_key:
                raise ValueError("CHAT_PROVIDER is set to 'openai' but OPENAI_API_KEY is not configured in .env.")
        elif chat_provider == "gemini":
            self.chat_model = os.getenv("CHAT_MODEL") or "gemini-1.5-flash"
            if not self.gemini_key:
                raise ValueError("CHAT_PROVIDER is set to 'gemini' but GEMINI_API_KEY is not configured in .env.")
        else:
            # Unknown provider — treat as no-chat
            self.chat_model = "none"

    def get_embedding(self, text: str) -> list[float]:
        """Generates a single text embedding vector."""
        if self.provider == "openai":
            return self._get_openai_embedding(text)
        elif self.provider == "ollama":
            return self._get_ollama_embedding(text)
        elif self.provider == "local":
            return self._get_local_embedding(text)
        else:
            return self._get_gemini_embedding(text)

    def get_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """Generates embeddings for a list of texts in batch for performance."""
        if not texts:
            return []
            
        if self.provider == "openai":
            return self._get_openai_embeddings_batch(texts)
        elif self.provider == "ollama":
            return self._get_ollama_embeddings_batch(texts)
        elif self.provider == "local":
            return self._get_local_embeddings_batch(texts)
        else:
            return self._get_gemini_embeddings_batch(texts)

    def generate_completion(self, system_instruction: str, prompt: str) -> str:
        """Generates a chat completion response from the configured LLM."""
        if self.chat_model == "none":
            raise RuntimeError("Chat completion is disabled when chat model is 'none'.")
        if self.chat_provider == "openai":
            return self._generate_openai_completion(system_instruction, prompt)
        elif self.chat_provider == "ollama":
            return self._generate_ollama_completion(system_instruction, prompt)
        else:
            return self._generate_gemini_completion(system_instruction, prompt)

    # --- OLLAMA IMPLEMENTATIONS ---
    
    def _get_ollama_embedding(self, text: str) -> list[float]:
        # Try /api/embed first, fallback to /api/embeddings
        try:
            url = f"{self.ollama_host}/api/embed"
            payload = {"model": self.embed_model, "input": text}
            res = requests.post(url, json=payload, timeout=15)
            if res.status_code == 200:
                return res.json()["embeddings"][0]
        except Exception:
            pass
            
        url = f"{self.ollama_host}/api/embeddings"
        payload = {"model": self.embed_model, "prompt": text}
        res = requests.post(url, json=payload, timeout=15)
        if res.status_code != 200:
            raise RuntimeError(f"Ollama embedding API error ({res.status_code}): {res.text}")
        return res.json()["embedding"]

    def _get_ollama_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        # Try batch via /api/embed
        try:
            url = f"{self.ollama_host}/api/embed"
            payload = {"model": self.embed_model, "input": texts}
            res = requests.post(url, json=payload, timeout=30)
            if res.status_code == 200:
                return res.json()["embeddings"]
        except Exception:
            pass
            
        # Fallback to serial requests
        embeddings = []
        for t in texts:
            embeddings.append(self._get_ollama_embedding(t))
        return embeddings

    def _generate_ollama_completion(self, system_instruction: str, prompt: str) -> str:
        url = f"{self.ollama_host}/api/chat"
        payload = {
            "model": self.chat_model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "options": {
                "temperature": 0.2
            }
         }
        res = requests.post(url, json=payload, timeout=45)
        if res.status_code != 200:
            raise RuntimeError(f"Ollama chat completion API error ({res.status_code}): {res.text}")
            
        data = res.json()
        try:
            return data["message"]["content"]
        except (KeyError, IndexError):
            raise RuntimeError(f"Unexpected response format from Ollama: {data}")

    # --- GEMINI IMPLEMENTATIONS ---
    
    def _get_gemini_embedding(self, text: str) -> list[float]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.embed_model}:embedContent?key={self.gemini_key}"
        payload = {
            "content": {
                "parts": [{"text": text}]
            }
        }
        res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
        if res.status_code != 200:
            raise RuntimeError(f"Gemini embedding API error ({res.status_code}): {res.text}")
        
        data = res.json()
        return data["embedding"]["values"]

    def _get_gemini_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.embed_model}:batchEmbedContents?key={self.gemini_key}"
        batch_size = 100
        embeddings = []
        
        for i in range(0, len(texts), batch_size):
            chunk_texts = texts[i:i+batch_size]
            requests_list = [
                {
                    "model": f"models/{self.embed_model}",
                    "content": {"parts": [{"text": t}]}
                }
                for t in chunk_texts
            ]
            
            payload = {"requests": requests_list}
            res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
            if res.status_code != 200:
                for t in chunk_texts:
                    embeddings.append(self._get_gemini_embedding(t))
                continue
                
            data = res.json()
            for item in data.get("embeddings", []):
                embeddings.append(item["values"])
                
        return embeddings

    def _generate_gemini_completion(self, system_instruction: str, prompt: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.chat_model}:generateContent?key={self.gemini_key}"
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"Instructions:\n{system_instruction}\n\nQuery:\n{prompt}"}]
                }
            ],
            "generationConfig": {
                "temperature": 0.2
            }
        }
        
        res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=45)
        if res.status_code != 200:
            raise RuntimeError(f"Gemini completion API error ({res.status_code}): {res.text}")
            
        data = res.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            raise RuntimeError(f"Unexpected response format from Gemini: {data}")

    # --- OPENAI IMPLEMENTATIONS ---
    
    def _get_openai_embedding(self, text: str) -> list[float]:
        url = "https://api.openai.com/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {self.openai_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "input": text,
            "model": self.embed_model
        }
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        if res.status_code != 200:
            raise RuntimeError(f"OpenAI embedding API error ({res.status_code}): {res.text}")
            
        data = res.json()
        return data["data"][0]["embedding"]

    def _get_openai_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        url = "https://api.openai.com/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {self.openai_key}",
            "Content-Type": "application/json"
        }
        
        batch_size = 500
        embeddings = []
        
        for i in range(0, len(texts), batch_size):
            chunk_texts = texts[i:i+batch_size]
            payload = {
                "input": chunk_texts,
                "model": self.embed_model
            }
            res = requests.post(url, json=payload, headers=headers, timeout=30)
            if res.status_code != 200:
                for t in chunk_texts:
                    embeddings.append(self._get_openai_embedding(t))
                continue
                
            data = res.json()
            sorted_data = sorted(data.get("data", []), key=lambda x: x["index"])
            for item in sorted_data:
                embeddings.append(item["embedding"])
                
        return embeddings

    def _generate_openai_completion(self, system_instruction: str, prompt: str) -> str:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.openai_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.chat_model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2
        }
        res = requests.post(url, json=payload, headers=headers, timeout=45)
        if res.status_code != 200:
            raise RuntimeError(f"OpenAI completion API error ({res.status_code}): {res.text}")
            
        data = res.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise RuntimeError(f"Unexpected response format from OpenAI: {data}")

    # --- LOCAL EMBEDDINGS (ONNX) IMPLEMENTATIONS ---

    def _init_local_model(self):
        if self._local_model is None:
            try:
                from fastembed import TextEmbedding
                model_name = self.embed_model if self.embed_model and self.embed_model != "none" else "BAAI/bge-small-en-v1.5"
                self._local_model = TextEmbedding(model_name=model_name)
            except ImportError:
                raise ImportError(
                    "The 'fastembed' library is required for local/offline embeddings.\n"
                    "Please install it using: pip install fastembed"
                )

    def _get_local_embedding(self, text: str) -> list[float]:
        self._init_local_model()
        embeddings = list(self._local_model.embed([text]))
        return [float(x) for x in embeddings[0]]

    def _get_local_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._init_local_model()
        embeddings = list(self._local_model.embed(texts))
        return [[float(x) for x in emb] for emb in embeddings]
