"""
main.py — FastAPI application for the AI Interview Simulator.

Endpoints:
  GET  /              Serve the frontend.
  GET  /health        Health check (verify server + API key status).
  POST /upload-resume/      Upload a PDF resume and extract text.
  GET  /generate-questions/  Generate AI interview questions from the resume.
  POST /evaluate/            Evaluate a candidate's answer to a question.
"""

import os
import re
import json
import logging
from pathlib import Path

from dotenv import load_dotenv

# Load .env BEFORE anything reads env vars
load_dotenv()

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from resume_parser import extract_text_from_pdf
from llm import (
    call_llm,
    FALLBACK_MESSAGE,
    SERVICE_BUSY_MESSAGE,
    PRIMARY_MODEL,
    FALLBACK_MODEL,
)

# ── Paths (resolved at startup so relative paths never break) ──────────────────
BACKEND_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
)
logger = logging.getLogger(__name__)

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="AI Interview Simulator",
    description="Upload your resume, get AI-generated interview questions, and receive structured feedback.",
    version="1.0.0",
)

# CORS — allow all origins during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Global Exception Handler ──────────────────────────────────────────────────
# Catches any unhandled exception and returns a clean JSON 503 instead of a
# raw stack trace leaking to the frontend.
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"🚨 Unhandled exception on {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=503,
        content={
            "status": "overloaded",
            "message": SERVICE_BUSY_MESSAGE,
            "detail": str(exc),
        },
    )

# ── In-Memory Store ────────────────────────────────────────────────────────────
# Simple dict-based state. Fine for a single-user MVP.
store: dict = {
    "resume_text": None,
    "questions": None,
}

# ── Models ─────────────────────────────────────────────────────────────────────

class EvaluateRequest(BaseModel):
    question: str = Field(..., min_length=5, description="The interview question.")
    answer: str = Field(..., min_length=1, description="The candidate's answer.")


class EvaluateResponse(BaseModel):
    score: int
    mistakes: list[str]
    improved_answer: str
    confidence_feedback: str


# ── Endpoints ──────────────────────────────────────────────────────────────────

def _repair_truncated_json(text: str) -> dict | None:
    """
    Attempt to repair truncated JSON by closing unclosed brackets, braces,
    and strings. Returns parsed dict/list on success, None on failure.
    """
    # Strip to the last } or ]
    cleaned = text.strip()
    
    # Try to find the opening brace
    start = cleaned.find("{")
    if start == -1:
        return None
    cleaned = cleaned[start:]
    
    # Count open/close braces and brackets
    in_string = False
    escape_next = False
    open_braces = 0
    open_brackets = 0
    
    for ch in cleaned:
        if escape_next:
            escape_next = False
            continue
        if ch == '\\':
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            open_braces += 1
        elif ch == '}':
            open_braces -= 1
        elif ch == '[':
            open_brackets += 1
        elif ch == ']':
            open_brackets -= 1
    
    # If we're inside a string, close it
    repaired = cleaned
    if in_string:
        repaired += '"'
    
    # Close any open brackets then braces
    repaired += ']' * open_brackets
    repaired += '}' * open_braces
    
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None

@app.get("/")
async def serve_frontend():
    """Serve the single-page frontend."""
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail=f"Frontend not found at {index_path}")
    return FileResponse(str(index_path))


@app.get("/health")
async def health_check():
    """Quick health check — verifies server is up and API key is configured."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    return {
        "status": "ok",
        "groq_api_key_set": bool(api_key),
        "groq_api_key_preview": f"{api_key[:8]}..." if len(api_key) > 8 else "(not set)",
        "primary_model": PRIMARY_MODEL,
        "fallback_model": FALLBACK_MODEL,
        "resume_loaded": store["resume_text"] is not None,
        "frontend_dir": str(FRONTEND_DIR),
        "frontend_exists": FRONTEND_DIR.exists(),
    }


@app.post("/upload-resume/")
async def upload_resume(file: UploadFile = File(...)):
    """
    Accept a PDF resume, extract text, and store it in memory.
    Returns the extracted text preview and character count.
    """
    logger.info(f"📄 Received upload: {file.filename}")

    # Validate file type
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are accepted. Please upload a .pdf file.",
        )

    # Read file bytes
    try:
        contents = await file.read()
    except Exception as e:
        logger.error(f"Failed to read uploaded file: {e}")
        raise HTTPException(status_code=400, detail="Failed to read the uploaded file.")

    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(contents) > 10 * 1024 * 1024:  # 10 MB limit
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 10 MB.")

    # Extract text from PDF
    try:
        text = extract_text_from_pdf(contents)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error parsing PDF: {e}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

    # Store in memory
    store["resume_text"] = text
    store["questions"] = None  # Reset questions on new upload

    logger.info(f"✅ Resume uploaded: {file.filename} ({len(text)} chars extracted)")
    return {
        "status": "success",
        "filename": file.filename,
        "characters_extracted": len(text),
        "preview": text[:500] + ("..." if len(text) > 500 else ""),
    }


@app.get("/generate-questions/")
async def generate_questions():
    """
    Generate 5 interview questions based on the stored resume text.
    Questions increase in difficulty and mix technical + behavioral.
    """
    if not store["resume_text"]:
        raise HTTPException(
            status_code=400,
            detail="No resume uploaded yet. Please upload a resume first.",
        )

    logger.info("🧠 Generating interview questions...")

    # Cap resume text to avoid eating the entire token budget
    resume_snippet = store["resume_text"][:1500]

    prompt = f"""Return ONLY valid JSON. No explanation, no markdown.

Generate 5 short interview questions for this resume:

{resume_snippet}

Exact format:
{{"questions": ["q1", "q2", "q3", "q4", "q5"]}}"""

    raw_response = None
    try:
        raw_response = await call_llm(prompt, temperature=0.7, max_tokens=1024)
        logger.info(f"🔍 Raw LLM response ({len(raw_response)} chars): {raw_response[:300]}")

        # Stage 1: Direct parse
        parsed = None
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError:
            logger.warning("Stage 1 (direct parse) failed.")

        # Stage 2: Regex extraction
        if parsed is None:
            match = re.search(r'\{.*\}', raw_response, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    logger.warning("Stage 2 (regex extraction) failed.")

        # Stage 3: Truncated JSON repair
        if parsed is None:
            logger.warning("Stage 3: Attempting truncated JSON repair...")
            parsed = _repair_truncated_json(raw_response)

        # Stage 4: Last resort — extract individual questions via regex
        if parsed is None:
            logger.warning("Stage 4: Extracting questions via regex fallback...")
            question_matches = re.findall(r'"question"\s*:\s*"([^"]+)"', raw_response)
            if not question_matches:
                # Try to grab any quoted strings that look like questions
                question_matches = re.findall(r'"([^"]{20,}\?)"', raw_response)
            if question_matches:
                parsed = {"questions": question_matches}
            else:
                raise ValueError(f"All 4 parse stages failed. Raw: {raw_response[:300]}")

        # Normalize: accept both {"questions": [...]} and bare [...]
        if isinstance(parsed, list):
            questions = [q.get("question", str(q)) if isinstance(q, dict) else str(q) for q in parsed]
        elif isinstance(parsed, dict) and "questions" in parsed:
            questions = [q.get("question", str(q)) if isinstance(q, dict) else str(q) for q in parsed["questions"]]
        else:
            raise ValueError("Unexpected response structure from LLM.")

        store["questions"] = questions
        logger.info(f"✅ Generated {len(questions)} interview questions.")

        return {"status": "success", "questions": questions}

    except ValueError as e:
        # JSON parsing failed — return error with raw response for debugging
        logger.error(f"❌ JSON parse failed: {e}")
        return {
            "status": "error",
            "error": "LLM returned invalid format",
            "raw_response": (raw_response or "")[:500],
            "detail": str(e),
        }
    except RuntimeError as e:
        # Both models exhausted — return clean 503
        logger.error(f"❌ All models failed: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "overloaded",
                "message": SERVICE_BUSY_MESSAGE,
            },
        )
    except Exception as e:
        logger.error(f"❌ Unexpected error in question generation: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "overloaded",
                "message": SERVICE_BUSY_MESSAGE,
            },
        )


@app.post("/evaluate/", response_model=EvaluateResponse)
async def evaluate_answer(req: EvaluateRequest):
    """
    Evaluate a candidate's answer against the interview question.
    Returns a score, mistakes, improved answer, and confidence feedback.
    """
    logger.info(f"📝 Evaluating answer for question: {req.question[:60]}...")

    prompt = f"""Return ONLY valid JSON. No explanation, no markdown.

Question: {req.question}
Answer: {req.answer}

Exact format:
{{"score": 7, "mistakes": ["mistake 1"], "improved_answer": "better answer", "confidence_feedback": "feedback"}}

Score 1-10. If empty or gibberish, score 1."""

    raw_response = None
    try:
        raw_response = await call_llm(prompt, temperature=0.3, max_tokens=1024)
        logger.info(f"🔍 Evaluate raw response ({len(raw_response)} chars): {raw_response[:300]}")

        # Stage 1: Direct parse
        parsed = None
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError:
            logger.warning("Evaluate Stage 1 (direct parse) failed.")

        # Stage 2: Regex extraction
        if parsed is None:
            match = re.search(r'\{.*\}', raw_response, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    logger.warning("Evaluate Stage 2 (regex extraction) failed.")

        # Stage 3: Truncated JSON repair
        if parsed is None:
            logger.warning("Evaluate Stage 3: Attempting truncated JSON repair...")
            parsed = _repair_truncated_json(raw_response)

        if parsed is None:
            raise ValueError(f"All parse stages failed. Raw: {raw_response[:300]}")

        # Ensure all required fields exist with proper types
        result = {
            "score": max(1, min(10, int(parsed.get("score", 1)))),
            "mistakes": parsed.get("mistakes", ["Could not analyze mistakes."]),
            "improved_answer": parsed.get("improved_answer", "No improved answer generated."),
            "confidence_feedback": parsed.get("confidence_feedback", "No confidence analysis available."),
        }

        logger.info(f"✅ Answer evaluated — Score: {result['score']}/10")
        return result

    except ValueError as e:
        # JSON parsing failed — return safe fallback instead of crashing
        logger.error(f"❌ JSON parse failed during evaluation: {e}")
        logger.error(f"   Raw response: {(raw_response or '')[:500]}")
        return {
            "score": 1,
            "mistakes": ["LLM returned invalid format. Please try again."],
            "improved_answer": "Could not generate — the AI response was malformed.",
            "confidence_feedback": "Evaluation unavailable due to a parsing error. Please retry.",
        }
    except RuntimeError as e:
        # Both models exhausted — return safe fallback with score=1
        logger.error(f"❌ All models failed during evaluation: {e}")
        return {
            "score": 1,
            "mistakes": [SERVICE_BUSY_MESSAGE],
            "improved_answer": SERVICE_BUSY_MESSAGE,
            "confidence_feedback": SERVICE_BUSY_MESSAGE,
        }
    except Exception as e:
        logger.error(f"❌ Unexpected error in evaluation: {e}")
        return {
            "score": 1,
            "mistakes": [SERVICE_BUSY_MESSAGE],
            "improved_answer": SERVICE_BUSY_MESSAGE,
            "confidence_feedback": SERVICE_BUSY_MESSAGE,
        }


# ── Serve Frontend Static Files ────────────────────────────────────────────────
# IMPORTANT: This mount MUST come AFTER all route definitions.
# FastAPI processes mounts in order — if this were first, it would
# intercept API routes like /generate-questions/ as static file lookups.
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")
    logger.info(f"📂 Frontend mounted from {FRONTEND_DIR}")
else:
    logger.warning(f"⚠️ Frontend directory not found at {FRONTEND_DIR}")


# ── Startup Log ────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_log():
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    logger.info("═" * 60)
    logger.info("🚀 AI Interview Simulator is running!")
    logger.info(f"   Frontend: {FRONTEND_DIR}")
    logger.info(f"   Groq key: {'✅ configured' if api_key else '❌ NOT SET — add to backend/.env'}")
    logger.info("═" * 60)
