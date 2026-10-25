"use strict";

const root = document.documentElement;
const themeToggle = document.querySelector(".theme-toggle");

function setTheme(theme) {
  root.dataset.theme = theme;
  const light = theme === "light";
  themeToggle.setAttribute("aria-pressed", String(light));
  themeToggle.setAttribute("aria-label", light ? "Switch to dark mode" : "Switch to light mode");
  themeToggle.querySelector(".theme-label").textContent = light ? "Dark" : "Light";
  themeToggle.querySelector(".theme-glyph").textContent = light ? "☾" : "☼";
  localStorage.setItem("sentience-theme", theme);
}

const savedTheme = localStorage.getItem("sentience-theme");
setTheme(savedTheme || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));

themeToggle.addEventListener("click", () => {
  setTheme(root.dataset.theme === "dark" ? "light" : "dark");
});

const featureContent = {
  chat: {
    index: "01",
    tag: "CORE EXPERIENCE",
    title: "Agent chat",
    lede: "A focused conversation layer that can work with the context you choose—not a blank chat window pretending it knows your computer.",
    body: "Start or resume conversations, switch between chat, reasoning, and code modes, add approved files, and let assigned models work through scoped tools. Provider responses, token usage, context limits, and local sources stay visible in the chat.",
    points: ["Multiple provider APIs", "Scoped file tools", "Conversation history"],
    command: "sentience > agent",
  },
  memory: {
    index: "02",
    tag: "LOCAL CONTEXT",
    title: "Memory & documents",
    lede: "Give Sentience the documents and preferences that matter, then keep the useful context available for the next question.",
    body: "Conversations and explicit memories live in local SQLite storage. Text files and DOCX documents can be attached directly, while PDFs are extracted into page-aware chunks and retrieved with optional embeddings when you need them.",
    points: ["SQLite storage", "PDF retrieval", "Explicit memories"],
    command: "agent > /add document.pdf",
  },
  argus: {
    index: "03",
    tag: "PROJECT AWARENESS",
    title: "Argus file history",
    lede: "See what changed, when it changed, and how that work relates to the project you were actually using.",
    body: "Argus combines filesystem watchers, Git shadow snapshots, line-level diffs, shell history, and active-window sessions. It turns activity into evidence you can inspect instead of vague claims about what happened.",
    points: ["Git-backed history", "Line-level diffs", "Activity evidence"],
    command: "sentience > argus",
  },
  sentinel: {
    index: "04",
    tag: "PRIVACY BOUNDARY",
    title: "Sentinel privacy",
    lede: "Sensitive context gets a decision before it becomes a model prompt.",
    body: "Sentinel scans content for secrets, credentials, high-entropy tokens, and personal data. Local mode keeps work on the machine; hybrid mode redacts secrets and tokenizes PII before eligible cloud requests; cloud mode is an explicit opt-out.",
    points: ["Secret detection", "PII tokenization", "Local-only routing"],
    command: "sentience > sentinel",
  },
  routing: {
    index: "05",
    tag: "MODEL CONTROL",
    title: "Model routing",
    lede: "Different work deserves different models—and you should be the one assigning them.",
    body: "Configure ordered local and online candidates for chat, reasoning, code, and embeddings. Sentience resolves the route, respects local-only constraints, and falls back only to models that are explicitly assigned to that task.",
    points: ["Task-based routes", "Local/cloud scopes", "Ordered fallbacks"],
    command: "sentience > routing",
  },
  connectors: {
    index: "06",
    tag: "EXTENSIBLE BY CHOICE",
    title: "Skills & connectors",
    lede: "Add capability without giving up the right to decide what is enabled.",
    body: "Custom skills shape how the agent helps. Approved connectors can search Gmail, work with Calendar, or look up flights and trains. External actions such as sending mail or creating events require explicit confirmation.",
    points: ["Custom skills", "Approved services", "Confirmed actions"],
    command: "sentience > skills",
  },
};

const detail = document.querySelector("#feature-detail");
const tabs = document.querySelectorAll(".feature-tab");

function showFeature(key) {
  const content = featureContent[key];
  if (!content) return;
  detail.innerHTML = `
    <div class="detail-top"><span class="detail-index">${content.index}</span><span class="detail-tag">${content.tag}</span></div>
    <h3>${content.title}</h3>
    <p class="detail-lede">${content.lede}</p>
    <p class="detail-body">${content.body}</p>
    <div class="detail-points">${content.points.map((point) => `<span>${point}</span>`).join("")}</div>
    <div class="detail-command"><span>Try it</span><code>${content.command}</code></div>
  `;
  tabs.forEach((tab) => {
    const active = tab.dataset.feature === key;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
}

tabs.forEach((tab) => tab.addEventListener("click", () => showFeature(tab.dataset.feature)));

const installCommands = {
  linux: "curl -fsSL https://raw.githubusercontent.com/nkc-gh/Sentience/main/Linux/install.sh | bash",
  windows: "irm https://raw.githubusercontent.com/nkc-gh/Sentience/main/Windows/install.ps1 | iex",
};
const platformTabs = document.querySelectorAll(".platform-tab");
const installCommand = document.querySelector("#install-command");
const commandPrompt = document.querySelector(".command-prompt");
const copyButton = document.querySelector(".install-command-row .copy-button");

function selectPlatform(platform) {
  installCommand.textContent = installCommands[platform];
  commandPrompt.textContent = platform === "linux" ? "$" : ">";
  platformTabs.forEach((tab) => {
    const active = tab.dataset.platform === platform;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  copyButton.textContent = "Copy command";
  copyButton.classList.remove("copied");
}

platformTabs.forEach((tab) => tab.addEventListener("click", () => selectPlatform(tab.dataset.platform)));
copyButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(installCommand.textContent.trim());
    copyButton.textContent = "Copied";
    copyButton.classList.add("copied");
    window.setTimeout(() => {
      copyButton.textContent = "Copy command";
      copyButton.classList.remove("copied");
    }, 1600);
  } catch {
    copyButton.textContent = "Select command";
    window.setTimeout(() => { copyButton.textContent = "Copy command"; }, 1600);
  }
});

const mediaFrames = document.querySelectorAll(".media-frame");
const mediaViewer = document.querySelector("#media-viewer");
const mediaViewerContent = document.querySelector("#media-viewer-content");
const mediaViewerCaption = document.querySelector("#media-viewer-caption");
const mediaViewerClose = document.querySelector(".media-viewer-close");
let lastMediaFrame = null;

function closeMediaViewer() {
  if (!mediaViewer.classList.contains("is-open")) return;
  mediaViewer.classList.remove("is-open");
  mediaViewer.setAttribute("aria-hidden", "true");
  document.body.classList.remove("media-viewer-open");
  window.setTimeout(() => mediaViewerContent.replaceChildren(), 270);
  lastMediaFrame?.focus();
}

function openMediaViewer(frame) {
  const source = frame.querySelector("img, video");
  const caption = frame.querySelector("figcaption")?.textContent || "Sentience preview";
  if (!source) return;

  lastMediaFrame = frame;
  const expandedMedia = source.cloneNode(true);
  if (expandedMedia.tagName === "VIDEO") {
    expandedMedia.removeAttribute("autoplay");
    expandedMedia.controls = true;
    expandedMedia.preload = "metadata";
  }
  mediaViewerContent.replaceChildren(expandedMedia);
  mediaViewerCaption.textContent = caption;
  mediaViewer.classList.add("is-open");
  mediaViewer.setAttribute("aria-hidden", "false");
  document.body.classList.add("media-viewer-open");
  mediaViewerClose.focus();
}

mediaFrames.forEach((frame) => {
  frame.addEventListener("click", (event) => {
    if (event.target.closest("video")) return;
    openMediaViewer(frame);
  });
  frame.addEventListener("keydown", (event) => {
    if (event.target.closest("video")) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openMediaViewer(frame);
    }
  });
});

mediaViewerClose.addEventListener("click", closeMediaViewer);
mediaViewer.addEventListener("click", (event) => {
  if (event.target === mediaViewer || event.target.classList.contains("media-viewer-backdrop")) {
    closeMediaViewer();
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeMediaViewer();
});
