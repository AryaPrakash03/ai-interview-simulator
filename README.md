# AI Interview Simulator (Resilient Edition)

An AI-powered SaaS MVP that generates interview questions from resumes and provides real-time feedback.

## 🚀 Key Features

* **Resume Parsing:** PDF text extraction using `pdfplumber`.
* **Intelligent Questioning:** Role-specific questions that increase in difficulty.
* **Production-Grade Resilience:**
  * **Tiered Fallback:** Automatically switches from Llama 3 to Mixtral if the primary provider is overloaded.
  * **Retry Logic:** Implements exponential backoff to handle 503 Service Unavailable errors.
* **FastAPI Backend:** High-performance asynchronous API handling.

## 🛠️ Tech Stack

* **Backend:** FastAPI, Python
* **AI Inference:** Groq Cloud (Llama 3, Mixtral)
* **Frontend:** Vanilla JS, CSS3, HTML5

## 🔧 Setup

1. Clone the repo.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` (or `backend/.env`) and add your `GROQ_API_KEY`:
   ```bash
   GROQ_API_KEY=your_groq_api_key_here
   ```
4. Run the server:
   ```bash
   python -m uvicorn backend.main:app --reload
   ```
