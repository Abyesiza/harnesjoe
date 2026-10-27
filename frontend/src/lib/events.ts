/* eslint-disable @typescript-eslint/no-explicit-any */
type Data = any;

export const words: Record<string, (d: Data) => string> = {
  Assigned: (d) => `${d.member} is taking task ${d.task.id}: ${d.task.text}`,
  ResearchRequest: (d) => `Quill started research job ${d.id}: ${d.question}`,
  Output: (d) => `${d.member} finished a first pass. The family is checking it.`,
  Green: () => "The tests passed. The family is reviewing the change.",
  Decision: (d) =>
    d.accept
      ? "The family accepted the change."
      : d.escalate
        ? "The family disagrees; Skipper needs your decision."
        : "The review did not pass. Skipper is looking at next steps.",
  Blocked: (d) => `Work is blocked: ${d.need}. Skipper needs your input.`,
  Applied: (d) => `Accepted changes were applied locally to ${d.path}. Nothing was pushed.`,
  ApplyPending: (d) => `${d.reason} The accepted work remains in its isolated worktree at ${d.task.cwd}.`,
  Question: (d) => `${d.member} needs your input on task ${d.task.id}: ${d.text}`,
  HumanAnswer: (d) =>
    d.answer.startsWith("(Skipper")
      ? `Skipper answered the family’s question on task ${d.task.id} for you.`
      : `You answered the family’s question on task ${d.task.id}.`,
  Error: (d) => `Skipper hit an error${d.task ? " on task " + d.task : ""}: ${d.message}`,
  Said: (d) => `${d.name}: ${d.text}`,
  Consulted: (d) => `${d.member} reported back on task ${d.id}: ${d.findings}`,
  Finding: (d) => `Quill finished research job ${d.id}: ${d.say || d.question}`,
  FolderApprovalNeeded: (d) => `Folder access requested for ${d.path}. Review it in “Folders & access”.`,
  FolderApproved: (d) => `Folder access approved for ${d.path}.`,
  Proposal: (d) => `A tool is available for review: ${d.names.join(", ")}. Say “approve <name>” if you want it.`,
  Scouted: (d) => `Ranger reviewed ${d.repo}. ${d.say || "The results are saved locally."}`,
  Focus: (d) => `The family is now focused on ${d.field}.`,
  Correction: () => "Skipper saved your guidance for future work.",
  Fallback: (d) => `${d.failed} failed (${d.reason}); retrying with ${d.to}.`,
};

export const REFRESH = ["Assigned", "ResearchRequest", "Finding", "Decision", "Applied", "ApplyPending", "Error", "Question", "HumanAnswer"];
export const ACTIVITY_TEXT = ["Proposal", "Scouted", "Lesson", "Focus"];
