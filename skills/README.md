# Skills Policy

This directory is the future source of truth for reusable skills shared across agent harnesses.

## Current Setup

The local Codex environment keeps a small implicit skill set for general work:

- `openai-docs`
- `skill-creator`
- `skill-installer`
- `github:github`
- `playwright-interactive`
- `screenshot`

Optional but useful skills remain available when relevant:

- `figma-implement-design`
- `figma:*`
- `imagegen`
- `plugin-creator`
- `review-agent`
- `find-skills`

Specialized design and output-control skills are installed but should be called explicitly. Their local `agents/openai.yaml` files set:

```yaml
policy:
  allow_implicit_invocation: false
```

This keeps them available without letting them influence unrelated coding or planning tasks.

## Explicit-Only Skills

- `brandkit`
- `design-taste-frontend`
- `design-taste-frontend-v1`
- `full-output-enforcement`
- `gpt-taste`
- `high-end-visual-design`
- `image-to-code`
- `imagegen-frontend-mobile`
- `imagegen-frontend-web`
- `industrial-brutalist-ui`
- `minimalist-ui`
- `redesign-existing-projects`
- `stitch-design-taste`

Call these manually when their style or workflow is specifically desired, for example:

```text
$gpt-taste build a motion-heavy landing page
$industrial-brutalist-ui design a tactical dashboard
```

## Fast Installation Notes

Use the Skills CLI for external skills:

```powershell
npx skills add <owner>/<repo>@<skill-name> -g -y
```

The `-g` flag installs the skill globally for the current user. The `-y` flag skips interactive confirmation.

Recommended coding and architecture skills to evaluate later:

```powershell
npx skills add addyosmani/agent-skills@context-engineering -g -y
npx skills add addyosmani/agent-skills@debugging-and-error-recovery -g -y
npx skills add addyosmani/agent-skills@api-and-interface-design -g -y
npx skills add addyosmani/agent-skills@observability-and-instrumentation -g -y
npx skills add mattpocock/skills@tdd -g -y
npx skills add mattpocock/skills@improve-codebase-architecture -g -y
```

Potential document cowork skills:

```powershell
npx skills add anthropics/skills@pdf -g -y
npx skills add anthropics/skills@docx -g -y
npx skills add anthropics/skills@xlsx -g -y
```

Graph-oriented codebase understanding tools such as Understand-Anything or DSP should be tested separately on a real codebase before being added to the default setup.

Plugins and connectors such as GitHub and Figma are installed through Codex plugin management, not through `npx skills add`.

## Principle

Keep general-purpose skills implicit. Keep strong, stylistic, or workflow-heavy skills explicit. This reduces accidental context usage and avoids specialized instructions leaking into ordinary tasks.
