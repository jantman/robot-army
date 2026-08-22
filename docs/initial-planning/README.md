# robot army project planning

A local application to help me delegate work to Claude Code.

The document at `github-claude-orchestrator-spec-skeleton.md` was an initial plan for something that only handles GitHub issues; this document also expands it to handle Trello issues as well.

* Daemon that runs in the background, as my user.
  * Web UI for monitoring and control
  * CLI or TUI for status monitoring
  * Can use MySQL (MariaDB) for storage if needed
* Detects GitHub issues on my repos (or a whitelist of repos that aren't mine) that are created by me and have a "robot-army" label on them
  * Spins up a local Claude Code session in a clone of the repo to work the issue
  * Claude Code explicitly enables remote control mode; the orchestrator just gives Claude Code a working directory and a prompt, a human drives via Remote Control after that point
  * Can set relative priority of repos (work all issues from higher-priority repo until done) or just work issues oldest to newest
* Has controls for concurrency, possibly also awareness of local claude code sessions that are running outside of the orchestrator's control (e.g. so that if I'm driving 3 claude code sessions interactively myself, that's taken into account as part of the concurrency cap)
* If possible, checks Claude Code usage/session limits and does not dispatch anything new if above a certain threshold
* Second part - Trello
  * Creates a Trello card on my board in the "In Progress" column pointing to each issue that's being worked; moves to Done when the issue is closed.
    * These also have a comment in a specific format to identify that they're being worked, something like `robot-army is working <issue URL>`
  * Looks for Trello cards with "AI-task" label
    * If they have a single clear repo URL or local path (`~/GIT/<repo name>`) in the description, create an issue matching the card, and comment on the card indicating that the issue has been created.
    * Otherwise, maintains a list of cards with this label that have insufficient information and surfaces that in the UI; human can tell it to re-scan those cards once they've been clarified.
* We don't want to overbuild this, but we do want to build it with the distinct possibility that it will become a general-purpose AI orchestrator. So, initial thoughts on abstractions:
  * GitHub Issues, Trello cards, etc. are specific implementations of a generic "work item" container, that has a source, a status, a flag for "needs more information from a human", etc.
  * GitHub and Trello are sources for work, again as concrete implementations of some "work item source"
  * Claude Code is the only current "AI worker" that items get dispatched to, but in the future we might include other items like direct Amazon Bedrock calls or local inference.

## Initial user flows

1. Trello card to issue - I create a Trello card with the "AI-task" label and a title of something like "privatepuppet - filter nuisance detections on garage monitor". It creates an issue in my `privatepuppet` github repo with a description of "filter nuisance detections on garage monitor". The card gets a comment pointing to the issue, that's identifiable as coming from robot-army.
2. Working a GitHub issue - I add the "robot-army" label to the above issue. Claude Code is launched with a configured set of CLI options in a clone of my `privatepuppet` repo and with an initial prompt of "filter nuisance detections on garage monitor".

## Stretch Goals

* Some sort of notification to me (pushover? slack?) when a new task is dispatched, or really when any signficant change happens
* Audit log viewable in web UI and with things like github repos, issues, trello cards, etc as clickable links
* I use `kitty` as my terminal. It would be super cool if new claude code sessions could be launched in new kitty windows, so that I could interact with them locally as if I'd started them myself

## Major Open Questions

1. Right now I do all of my work in persistent clones of my git repos under `~/GIT/`. This would obviously be a problem if the orchestrator launches a claude code session in a directory that I'm also simultaneously doing interactive work in. What's the solution? Should the orchestrator make its own clones? Or do I need to convert all of my existing ones to worktrees?
