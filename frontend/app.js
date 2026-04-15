/**
 * app.js — AI Interview Simulator Frontend Logic
 * 
 * Handles: file upload, question generation, answer submission,
 * feedback display, progress tracking, and UI state management.
 */

// ── Configuration ─────────────────────────────────────────────────────────────
const API_BASE = window.location.origin;

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  resumeUploaded: false,
  questions: [],
  answers: {},       // { questionId: answerText }
  feedback: {},      // { questionId: feedbackObj }
  evaluating: {},    // { questionId: boolean } — tracks loading per question
};

// ── DOM References ────────────────────────────────────────────────────────────
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const dom = {
  uploadZone: $("#upload-zone"),
  fileInput: $("#file-input"),
  fileInfo: $("#file-info"),
  fileName: $("#file-name"),
  fileChars: $("#file-chars"),
  startBtn: $("#start-btn"),
  preInterview: $("#pre-interview"),
  questionsLoading: $("#questions-loading"),
  interviewActive: $("#interview-active"),
  questionsList: $("#questions-list"),
  progressText: $("#progress-text"),
  progressFill: $("#progress-fill"),
  summarySection: $("#summary-section"),
  summaryCard: $("#summary-card"),
  toastContainer: $("#toast-container"),
};

// ── Toast Notifications ───────────────────────────────────────────────────────

function showToast(message, type = "info") {
  const icons = { success: "✅", error: "❌", info: "ℹ️" };
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span>${icons[type] || ""}</span><span>${message}</span>`;
  dom.toastContainer.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateX(100%)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// ── Step Indicator ────────────────────────────────────────────────────────────

function updateSteps(activeStep) {
  for (let i = 1; i <= 3; i++) {
    const step = $(`#step-${i}`);
    const connector = $(`#connector-${i - 1}-${i}`);
    step.classList.remove("active", "done");
    if (i < activeStep) {
      step.classList.add("done");
      if (connector) connector.classList.add("done");
    } else if (i === activeStep) {
      step.classList.add("active");
    }
  }
}

// ── File Upload ───────────────────────────────────────────────────────────────

// Click to open file picker
dom.uploadZone.addEventListener("click", () => dom.fileInput.click());

// Keyboard accessibility
dom.uploadZone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    dom.fileInput.click();
  }
});

// Drag and drop
dom.uploadZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dom.uploadZone.classList.add("drag-over");
});

dom.uploadZone.addEventListener("dragleave", () => {
  dom.uploadZone.classList.remove("drag-over");
});

dom.uploadZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dom.uploadZone.classList.remove("drag-over");
  const files = e.dataTransfer.files;
  if (files.length > 0) handleFileUpload(files[0]);
});

// File input change
dom.fileInput.addEventListener("change", (e) => {
  if (e.target.files.length > 0) handleFileUpload(e.target.files[0]);
});

async function handleFileUpload(file) {
  // Client-side validation
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    showToast("Please upload a PDF file.", "error");
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    showToast("File is too large. Maximum 10 MB.", "error");
    return;
  }

  // Show uploading state
  dom.uploadZone.style.pointerEvents = "none";
  dom.uploadZone.style.opacity = "0.6";
  showToast("Uploading resume…", "info");

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch(`${API_BASE}/upload-resume/`, {
      method: "POST",
      body: formData,
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Upload failed.");
    }

    const data = await res.json();
    state.resumeUploaded = true;

    // Update UI
    dom.fileName.textContent = data.filename;
    dom.fileChars.textContent = `${data.characters_extracted.toLocaleString()} chars`;
    dom.fileInfo.classList.remove("hidden");
    dom.startBtn.disabled = false;

    showToast("Resume uploaded successfully!", "success");
  } catch (err) {
    showToast(err.message || "Failed to upload resume.", "error");
  } finally {
    dom.uploadZone.style.pointerEvents = "";
    dom.uploadZone.style.opacity = "";
  }
}

// ── Start Interview ───────────────────────────────────────────────────────────

dom.startBtn.addEventListener("click", startInterview);

async function startInterview() {
  if (!state.resumeUploaded) {
    showToast("Upload a resume first.", "error");
    return;
  }

  // Transition to loading state
  dom.preInterview.classList.add("hidden");
  dom.questionsLoading.classList.remove("hidden");
  updateSteps(2);

  try {
    const res = await fetch(`${API_BASE}/generate-questions/`);
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to generate questions.");
    }

    const data = await res.json();
    state.questions = data.questions;

    // Render questions
    renderQuestions(state.questions);

    // Show interview panel
    dom.questionsLoading.classList.add("hidden");
    dom.interviewActive.classList.remove("hidden");

    showToast("Interview questions ready. Good luck! 🍀", "success");
  } catch (err) {
    showToast(err.message || "Failed to generate questions.", "error");
    // Revert to pre-interview state
    dom.questionsLoading.classList.add("hidden");
    dom.preInterview.classList.remove("hidden");
  }
}

// ── Render Questions ──────────────────────────────────────────────────────────

function renderQuestions(questions) {
  dom.questionsList.innerHTML = "";

  questions.forEach((q, idx) => {
    const card = document.createElement("div");
    card.className = "question-card";
    card.id = `question-${q.id}`;
    card.dataset.id = q.id;

    const difficultyClass = {
      basic: "badge-basic",
      intermediate: "badge-intermediate",
      advanced: "badge-advanced",
    }[q.difficulty] || "badge-basic";

    const categoryClass = q.category === "behavioral" ? "badge-behavioral" : "badge-technical";

    card.innerHTML = `
      <div class="question-header">
        <span class="question-number">${q.id}</span>
        <span class="question-badge ${difficultyClass}">${q.difficulty}</span>
        <span class="question-badge ${categoryClass}">${q.category}</span>
      </div>
      <p class="question-text">${escapeHtml(q.question)}</p>
      <div class="answer-area" id="answer-area-${q.id}">
        <textarea
          class="answer-textarea"
          id="answer-input-${q.id}"
          placeholder="Type your answer here…"
          rows="4"
        ></textarea>
        <div class="answer-actions">
          <button class="btn btn-primary" id="submit-btn-${q.id}" onclick="submitAnswer(${q.id})">
            Submit Answer
          </button>
        </div>
      </div>
      <div id="feedback-${q.id}"></div>
    `;

    dom.questionsList.appendChild(card);
  });
}

// ── Submit Answer ─────────────────────────────────────────────────────────────

async function submitAnswer(questionId) {
  const textarea = $(`#answer-input-${questionId}`);
  const submitBtn = $(`#submit-btn-${questionId}`);
  const answer = textarea.value.trim();

  if (!answer) {
    showToast("Please type an answer before submitting.", "error");
    return;
  }

  // Find the question text
  const question = state.questions.find((q) => q.id === questionId);
  if (!question) return;

  // Loading state
  state.evaluating[questionId] = true;
  submitBtn.disabled = true;
  submitBtn.innerHTML = `<span class="spinner"></span> Evaluating…`;
  textarea.disabled = true;

  try {
    const res = await fetch(`${API_BASE}/evaluate/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: question.question,
        answer: answer,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Evaluation failed.");
    }

    const feedback = await res.json();
    state.answers[questionId] = answer;
    state.feedback[questionId] = feedback;

    // Render feedback
    renderFeedback(questionId, feedback);

    // Mark card as completed
    $(`#question-${questionId}`).classList.add("completed");

    // Update progress
    updateProgress();

    // Hide answer area after submission
    const answerArea = $(`#answer-area-${questionId}`);
    answerArea.classList.add("hidden");

    showToast(`Question ${questionId} evaluated — Score: ${feedback.score}/10`, "success");

  } catch (err) {
    showToast(err.message || "Failed to evaluate answer.", "error");
    submitBtn.disabled = false;
    submitBtn.innerHTML = "Submit Answer";
    textarea.disabled = false;
  } finally {
    state.evaluating[questionId] = false;
  }
}

// ── Render Feedback ───────────────────────────────────────────────────────────

function renderFeedback(questionId, feedback) {
  const container = $(`#feedback-${questionId}`);
  const scoreColor = getScoreColor(feedback.score);
  const scorePct = feedback.score * 10;

  const mistakesHtml = feedback.mistakes.length > 0
    ? `<ul>${feedback.mistakes.map((m) => `<li>${escapeHtml(m)}</li>`).join("")}</ul>`
    : `<p>No significant mistakes found. Well done!</p>`;

  container.innerHTML = `
    <div class="feedback-panel">
      <div class="feedback-header">
        <div class="score-display">
          <div class="score-circle" style="--score-color:${scoreColor}; --score-pct:${scorePct}; color:${scoreColor};">
            ${feedback.score}
          </div>
          <div>
            <div class="score-label">Score</div>
            <div class="score-value" style="color:${scoreColor}">${feedback.score}/10</div>
          </div>
        </div>
      </div>

      <div class="feedback-section">
        <div class="feedback-section-title">⚠️ Areas for Improvement</div>
        ${mistakesHtml}
      </div>

      <div class="feedback-section">
        <div class="feedback-section-title">✅ Improved Answer</div>
        <div class="improved-answer">
          ${escapeHtml(feedback.improved_answer)}
        </div>
      </div>

      <div class="feedback-section">
        <div class="feedback-section-title">💡 Confidence Analysis</div>
        <p>${escapeHtml(feedback.confidence_feedback)}</p>
      </div>
    </div>
  `;
}

// ── Progress Tracking ─────────────────────────────────────────────────────────

function updateProgress() {
  const total = state.questions.length;
  const answered = Object.keys(state.feedback).length;
  const pct = total > 0 ? Math.round((answered / total) * 100) : 0;

  dom.progressText.textContent = `${answered} / ${total} answered`;
  dom.progressFill.style.width = `${pct}%`;

  // All done? Show summary
  if (answered === total && total > 0) {
    setTimeout(() => showSummary(), 600);
  }
}

// ── Summary ───────────────────────────────────────────────────────────────────

function showSummary() {
  updateSteps(3);
  dom.summarySection.classList.remove("hidden");

  const scores = Object.values(state.feedback).map((f) => f.score);
  const avgScore = (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(1);
  const maxScore = Math.max(...scores);
  const minScore = Math.min(...scores);

  const avgColor = getScoreColor(Math.round(avgScore));

  dom.summaryCard.innerHTML = `
    <p style="font-size:1rem; color:var(--text-secondary);">Your Average Score</p>
    <div class="summary-score" style="-webkit-text-fill-color:${avgColor}">${avgScore}</div>
    <p class="summary-label">${getScoreMessage(avgScore)}</p>
    <div class="summary-stats">
      <div class="stat-item">
        <div class="stat-value">${scores.length}</div>
        <div class="stat-label">Questions</div>
      </div>
      <div class="stat-item">
        <div class="stat-value" style="color:var(--accent-emerald)">${maxScore}</div>
        <div class="stat-label">Best Score</div>
      </div>
      <div class="stat-item">
        <div class="stat-value" style="color:var(--accent-rose)">${minScore}</div>
        <div class="stat-label">Lowest Score</div>
      </div>
    </div>
    <button class="btn btn-secondary btn-lg btn-full" style="margin-top:1.5rem;" onclick="resetInterview()">
      🔄 Start New Interview
    </button>
  `;

  // Scroll to summary
  dom.summarySection.scrollIntoView({ behavior: "smooth", block: "center" });
}

function resetInterview() {
  state.resumeUploaded = false;
  state.questions = [];
  state.answers = {};
  state.feedback = {};
  state.evaluating = {};

  dom.fileInfo.classList.add("hidden");
  dom.startBtn.disabled = true;
  dom.preInterview.classList.remove("hidden");
  dom.interviewActive.classList.add("hidden");
  dom.questionsLoading.classList.add("hidden");
  dom.summarySection.classList.add("hidden");
  dom.questionsList.innerHTML = "";
  dom.progressFill.style.width = "0%";
  dom.progressText.textContent = "0 / 5 answered";
  dom.fileInput.value = "";

  updateSteps(1);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function getScoreColor(score) {
  if (score >= 8) return "#34d399"; // emerald
  if (score >= 6) return "#6366f1"; // indigo
  if (score >= 4) return "#fbbf24"; // amber
  return "#fb7185";                 // rose
}

function getScoreMessage(avg) {
  if (avg >= 8) return "Outstanding performance! You're well-prepared. 🏆";
  if (avg >= 6) return "Solid showing. A few areas to polish up. 👍";
  if (avg >= 4) return "Decent effort. Review the feedback and practice more. 📚";
  return "Needs significant improvement. Study the improved answers. 💪";
}
