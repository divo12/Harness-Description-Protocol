# Harness Definition Protocol (HDP): Literature Review
*Generated: 2026-06-12 | Sources: 35+ | Confidence: High (all anchor claims verified against live sources)*

## Executive Summary

The literature has converged, in roughly 18 months, on the thesis HDP starts from: **the harness — not the model — is the reliability layer of LLM agents**. Three distinct research streams now exist: (1) **automated harness evolution** (AHE, Meta-Harness, HarnessForge), which proves harnesses can be optimized by closed agentic loops; (2) **harness taxonomies** (the ETCLOVG survey, the Externalization review, context-engineering surveys), which give the field a shared component vocabulary; and (3) **declarative agent specifications** (Oracle's Open Agent Spec, Agent Format, Microsoft's Agent Control Specification), which pursue "define once, run anywhere" portability. **No existing work sits at the intersection**: a portable, definitional schema that covers the *full* harness (middleware, skills, memory, permissions — not just workflow graphs), is *evolution-ready* (editability governance + falsifiable change manifests as schema objects), and treats *safety as schema* (blast radius, policy immunity). That intersection is HDP's defensible position — but the review also shows the "vendor-neutral agent YAML" idea **on its own is no longer novel**, so HDP must be framed against Agent Spec et al., not in a vacuum.

> **Verification note:** the **ETCLOVG** taxonomy is real and citable — it is the seven-layer framework (Execution, Tooling, Context, Lifecycle, Observability, Verification, Governance) of *Agent Harness Engineering: A Survey* (under review at TMLR). Earlier doubt about the acronym is resolved.

---

## 1. Automated Harness Evolution — the direct lineage

**The anchor.** *Agentic Harness Engineering: Observability-Driven Automatic Evolution of Coding-Agent Harnesses* ([arXiv:2604.25850](https://arxiv.org/abs/2604.25850), Fudan/PKU/Shanghai Qiji Zhifeng, Apr 2026; v4 May 2026) introduces the three observability pillars (component / experience / decision) and lifts Terminal-Bench 2 pass@1 69.7→77.0% on GPT-5.4 over ten iterations, beating the human-designed Codex harness and self-evolve baselines; the [official repo](https://github.com/china-qijizhifeng/agentic-harness-engineering) reports 84.7% ± 2.1 on GPT-5.5. **Optimizes:** all 7 file-level harness components. **Holds fixed:** model weights, verifier, LLM config. AHE is the only work with *file-level component observability* + *falsifiable per-edit predictions* — the two mechanisms HDP elevates into protocol obligations.

**Concurrent work.** *Meta-Harness: End-to-End Optimization of Model Harnesses* ([arXiv:2603.28052](https://arxiv.org/abs/2603.28052), Lee, Nair, Qizheng Zhang, Kangwook Lee, Khattab, Finn — Stanford, Mar 2026) frames the harness as **searchable code**: an agentic proposer reads source, scores, and traces of all prior candidates through a filesystem. Headline: changing only the harness around a fixed LLM yields up to a **6× performance gap**; a discovered harness transfers across five held-out models. **Optimizes:** harness code wholesale (no component decomposition). The AHE repo itself flags the two as concurrent. Notably, Qizheng Zhang bridges ACE and Meta-Harness — the prompt-evolution and harness-evolution lineages are merging.

**The newest wave (mid-2026)** confirms this is now a subfield:
- *HarnessForge: Joint Harness and Policy Evolution for Adaptive Agent Systems* ([arXiv:2606.01779](https://arxiv.org/abs/2606.01779)) — co-evolves harness *and* policy.
- *Adapting the Interface, Not the Model: Runtime Harness Adaptation for Deterministic LLM Agents* ([arXiv:2605.22166](https://arxiv.org/html/2605.22166v1)) — harness adaptation at runtime rather than between evaluation rounds.
- *From Failed Trajectories to Reliable LLM Agents: Diagnosing and Repairing Harness Flaws* ([arXiv:2606.06324](https://arxiv.org/html/2606.06324v1)) — trajectory-driven harness debugging, i.e. AHE's "experience observability" as a standalone problem.

**The self-evolve baselines AHE beat** (each evolves a *narrower* surface):
- **ACE** — *Agentic Context Engineering: Evolving Contexts for Self-Improving Language Models* ([arXiv:2510.04618](https://arxiv.org/abs/2510.04618)): contexts as evolving itemized "playbooks" via Generator/Reflector/Curator; +10.6% on agents, matches top AppWorld production agents. **Optimizes:** in-context playbook only.
- **Training-Free GRPO** ([arXiv:2510.08191](https://arxiv.org/abs/2510.08191), Tencent Youtu): distills experiential knowledge as a token prior injected at API call time; no parameter updates. **Optimizes:** prompt-level experience tokens only.
- **GEPA** — *Reflective Prompt Evolution Can Outperform Reinforcement Learning* ([arXiv:2507.19457](https://arxiv.org/abs/2507.19457)): Pareto-frontier reflective prompt evolution; beats GRPO by ~6-20% with up to 35× fewer rollouts; beats MIPROv2 by >10%. **Optimizes:** prompts in a fixed pipeline.

**Why this matters for HDP:** the loop papers prove harness evolution *works* but each invents a bespoke, framework-locked substrate (NexAU files; harness-as-code; playbook JSON). None defines a portable format another system could consume. That substrate gap is HDP's opening.

## 2. The scaffold-search lineage (pre-harness vocabulary)

The 2024-2025 wave optimized *agent designs* before "harness" became the term:
- **ADAS / Meta Agent Search** ([arXiv:2408.08435](https://arxiv.org/abs/2408.08435), ICLR 2025): a meta-agent programs new agents in code against a growing archive. **Search space:** whole agent programs.
- **AgentSquare** (modular agent search): decomposes agents into Planning / Reasoning / Tool-use / Memory modules and evolves module combinations — the earliest "typed component" search space ([Semantic Scholar](https://www.semanticscholar.org/paper/Automated-Design-of-Agentic-Systems-Hu-Lu/c9537f656e7d9713fd4108ce7bf512290f48e562) citing context).
- **Darwin Gödel Machine** ([arXiv:2505.22954](https://arxiv.org/html/2505.22954v2), Sakana/Clune, May 2025): open-ended archive-based evolution of self-improving coding agents that **edit their own code** — ADAS where the optimizer is also the optimizee.
- **AgentFactory** ([arXiv:2603.18000](https://arxiv.org/pdf/2603.18000), Mar 2026): self-evolution through executable **subagent** accumulation and reuse.

**Relevance:** these establish that (a) typed/modular search spaces beat free-form code search for attribution, and (b) self-modification needs governance — both arguments HDP encodes structurally.

## 3. Harness taxonomies — the vocabulary HDP must align with

- ***Agent Harness Engineering: A Survey*** ([OpenReview, under review at TMLR](https://openreview.net/forum?id=3hXEPbG0dh); [companion repo: 110+ papers, 23 systems analyzed](https://github.com/Gloriaameng/Awesome-Agent-Harness)) — defines the **ETCLOVG** seven layers: **E**xecution environment, **T**ool interface, **C**ontext management, **L**ifecycle/orchestration, **O**bservability, **V**erification, **G**overnance. E/T/C/L are structural pillars; O monitors system-wide; V evaluates; G enforces permissions and audit. This is the closest thing to a community-standard decomposition and should be HDP's cross-walk target (HDP's five domains + typed components map onto it cleanly; HDP adds the *definitional/generative* layer the survey doesn't attempt).
- ***Externalization in LLM Agents: A Unified Review of Memory, Skills, Protocols and Harness Engineering*** ([arXiv:2604.08224](https://arxiv.org/abs/2604.08224), 21 authors, Apr 2026) — frames infrastructure via cognitive-artifacts theory: memory externalizes state, skills externalize procedure, protocols externalize interaction, and the harness is "the unification layer that coordinates [them] into governed execution." Its stated open challenges — **self-evolving harnesses, shared agent infrastructure, model-infrastructure co-evolution** — read as a near-direct call for HDP.
- ***A Survey of Context Engineering for LLMs*** ([arXiv:2507.13334](https://arxiv.org/abs/2507.13334), 1400+ papers) — the foundational decomposition of the context layer (retrieval/generation, processing, management → RAG, memory, tool-integrated reasoning, multi-agent).
- ***Everything is Context: Agentic File System Abstraction for Context Engineering*** ([arXiv:2512.05470](https://arxiv.org/pdf/2512.05470)) — argues for file-system-as-context-substrate, independent support for AHE/HDP's file-level component representation.
- Practitioner taxonomies corroborate: [*The Harness Is the Reliability Layer*](https://www.antoinebuteau.com/the-harness-is-the-reliability-layer/) (essay), [MindStudio's "9 components every production harness needs"](https://www.mindstudio.ai/blog/9-components-production-agent-harness), [Atlan's harness explainer](https://atlan.com/know/what-is-an-agent-harness/).

## 4. Declarative agent specifications — HDP's direct competitors

This space is **crowded as of late 2025-2026**; HDP's draft must position against it explicitly.

| Spec | Owner | Format | Covers | Does NOT cover (verified) |
|---|---|---|---|---|
| **Open Agent Spec (Agent Spec)** ([arXiv:2510.04173](https://arxiv.org/html/2510.04173v1); [Oracle blog](https://blogs.oracle.com/ai-and-datascience/introducing-open-agent-specification)) | Oracle, Oct 2025 | JSON (YAML ok) | Agents, cyclic Flows (typed nodes/edges), LLM configs, tools; runs on LangGraph/AutoGen/OCI/WayFlow; "ONNX for agents" | **Memory (future), middleware, permissions/sandboxing, self-evolution, change manifests, embedded code** |
| **Agent Format (.agf.yaml)** ([agentformat.org](https://agentformat.org/)) | open project | YAML | "K8s manifest for agents" — declares what an agent needs; runtime decides execution | harness internals (middleware/skills), evolution loop |
| **AgentSpec** ([agents-oss.github.io/agentspec](https://agents-oss.github.io/agentspec/)) | OSS | YAML (`agent.yaml`) | model, memory, tools, guardrails, eval, observability in one file | component-level evolution, generative compilation, per-edit attribution |
| **Agent Control Specification (ACS)** ([Microsoft](https://microsoft.github.io/agent-governance-toolkit/packages/agent-control-specification/)) | Microsoft | manifest | portable **runtime governance**: where/when/how policies are enforced across the agent lifecycle, framework-independent | the harness definition itself (governance-only — complementary to HDP's Permissions domain) |
| **gitagent** ([GitHub](https://github.com/open-gitagent/gitagent)) | OSS | git repo of files | "agent lives in a git repo" — identity, rules, memory, tools, skills as version-controlled files | a formal schema/validator; evolution contracts |
| **MCP / A2A / ACP / ANP** (survey: [arXiv:2505.02279](https://arxiv.org/abs/2505.02279)) | Anthropic / Google / IBM / — | JSON-RPC etc. | **interaction protocols**: tool invocation (MCP), capability-advertising Agent Cards (A2A), REST messaging (ACP) | the harness layer entirely — these are what HDP *references*, not competes with |
| **agents.md / CLAUDE.md / SKILL.md** conventions | community/Anthropic | Markdown | instructions, skills-as-files | schema, validation, typed components, governance |

**The verified gap:** Agent Spec — the strongest competitor — **explicitly excludes** memory (future work), middleware, permissions/sandboxing, self-evolution loops, and change manifests, and forbids embedded executable code. ACS covers governance but not definition. AgentSpec(.io) covers breadth but has no evolution/attribution story. **No spec is simultaneously harness-complete, evolution-ready, and safety-first.**

## 5. Self-evolving skills & memory (the content HDP's Knowledge domain carries)

- **Voyager** (2023, foundational): ever-growing skill library of verified executable programs, retrieved compositionally — the prototype of skills-as-files ([overview](https://beancount.io/bean-labs/research-logs/2026/05/08/voyager-open-ended-embodied-agent-lifelong-learning)).
- **ExpeL**: distills past trajectories into insights/rules; +18%/+12% on ALFWorld/WebShop without weight updates.
- **Agent Workflow Memory**: records reusable sub-task workflows for retrieval at task time.
- **Surveys:** *A Survey of Self-Evolving Agents* ([arXiv:2507.21046](https://arxiv.org/html/2507.21046v4) — what/when/how/where to evolve); *Adaptation of Agentic AI: Post-Training, Memory, and Skills* ([arXiv:2512.16301](https://arxiv.org/pdf/2512.16301)); *Memory in the Age of AI Agents* ([arXiv:2512.13564](https://arxiv.org/pdf/2512.13564)); *ReCreate* ([arXiv:2601.11100](https://arxiv.org/pdf/2601.11100)) — experience-driven domain-agent creation.

**Relevance:** AHE's ablation found long-term memory was the single largest transferable gain. The skills/memory literature supplies the *content lifecycle* (discover → add → test → version) that HDP's Knowledge domain and evolution sidecar must support.

## 6. Safety & governance of self-modifying agents

- **Threat framing:** OWASP lists Prompt Injection and **Excessive Agency** among top LLM threats; the *Self-Evolving Agents* survey calls for "prescriptive, actionable guardrails" for self-evolution specifically.
- **MIT's 2025 AI Agent Index** ([PDF](https://aiagentindex.mit.edu/data/2025-AI-Agent-Index.pdf)) documents guardrails/sandboxing/evals/third-party testing across deployed agents — evidence that governance documentation is becoming an expected artifact (HDP makes it machine-readable).
- **Mechanisms:** *Policy-as-Prompt* ([arXiv:2509.23994](https://arxiv.org/abs/2509.23994)) — governance rules → runtime guardrails; *AgentGuardian* ([arXiv:2601.10440](https://arxiv.org/pdf/2601.10440)) — learned access-control policies for agent behavior; *AgentDoG* ([arXiv:2601.18491](https://arxiv.org/pdf/2601.18491)) — diagnostic guardrail framework; *Rethinking Autonomy* ([arXiv:2508.11824](https://arxiv.org/pdf/2508.11824)) — failure prevention in AI-driven software engineering, sandbox + static-analysis gating for agent-generated tools.
- **Precedent inside the lineage:** AHE's own "controllability" constraints (read-only verifier/model config, non-deletable seed prompt) are prose conventions — HDP's contribution is promoting exactly these to schema-enforced rules (cf. Microsoft ACS doing the same for runtime policy).

## Key Takeaways (for the HDP draft)

1. **Cite ETCLOVG confidently** — it's the TMLR-track *Agent Harness Engineering: A Survey*; cross-walk HDP's 5 domains to its 7 layers in a table.
2. **Reframe novelty as the intersection, not the schema.** "Vendor-neutral agent YAML" exists (Agent Spec, Agent Format, AgentSpec, ACS). HDP's defensible claim: *the first harness-complete, evolution-ready, safety-as-schema definition protocol* — i.e., the missing substrate that AHE/Meta-Harness/HarnessForge each had to invent bespoke.
3. **Lead the gap analysis with Agent Spec's verified exclusions** (memory/middleware/permissions/evolution/change-manifests) — it's the strongest competitor and its own technical report concedes HDP's territory.
4. **Position MCP/A2A as the layer below, not competitors** — HDP *references* MCP servers in its Tools domain; the interoperability survey (2505.02279) gives the clean layering citation.
5. **The Externalization review (2604.08224) is your "call to action" citation** — its named open challenges (self-evolving harnesses, shared infrastructure, model-infra co-evolution) are precisely HDP's pitch.
6. **Answer to the draft's open question:** include industry frameworks. The decisive prior art (Agent Spec, ACS, Agent Format, MCP) is industrial; an academic-only review would miss HDP's actual competition. LangChain/Vercel SDK configs belong in §4 as framework-locked counterexamples.
7. **Timing argument:** harness evolution went from one paper (AHE, Apr 2026) to a subfield (HarnessForge, runtime adaptation, harness-flaw repair — May/Jun 2026) in three months. Every one of these loops needs a substrate; none has a standard. That's the window.

## Sources

**Harness evolution:** [AHE arXiv:2604.25850](https://arxiv.org/abs/2604.25850) · [AHE repo](https://github.com/china-qijizhifeng/agentic-harness-engineering) · [Meta-Harness arXiv:2603.28052](https://arxiv.org/abs/2603.28052) · [HarnessForge arXiv:2606.01779](https://arxiv.org/abs/2606.01779) · [Runtime Harness Adaptation arXiv:2605.22166](https://arxiv.org/html/2605.22166v1) · [Harness Flaws arXiv:2606.06324](https://arxiv.org/html/2606.06324v1) · [ACE arXiv:2510.04618](https://arxiv.org/abs/2510.04618) · [TF-GRPO arXiv:2510.08191](https://arxiv.org/abs/2510.08191) · [GEPA arXiv:2507.19457](https://arxiv.org/abs/2507.19457)
**Scaffold search:** [ADAS arXiv:2408.08435](https://arxiv.org/abs/2408.08435) · [DGM arXiv:2505.22954](https://arxiv.org/html/2505.22954v2) · [AgentFactory arXiv:2603.18000](https://arxiv.org/pdf/2603.18000)
**Taxonomies:** [Agent Harness Engineering: A Survey (OpenReview/TMLR)](https://openreview.net/forum?id=3hXEPbG0dh) · [Awesome-Agent-Harness repo](https://github.com/Gloriaameng/Awesome-Agent-Harness) · [Externalization review arXiv:2604.08224](https://arxiv.org/abs/2604.08224) · [Context Engineering survey arXiv:2507.13334](https://arxiv.org/abs/2507.13334) · [Everything is Context arXiv:2512.05470](https://arxiv.org/pdf/2512.05470)
**Specifications:** [Open Agent Spec arXiv:2510.04173](https://arxiv.org/html/2510.04173v1) · [Oracle announcement](https://blogs.oracle.com/ai-and-datascience/introducing-open-agent-specification) · [Agent Format](https://agentformat.org/) · [AgentSpec](https://agents-oss.github.io/agentspec/) · [Microsoft ACS](https://microsoft.github.io/agent-governance-toolkit/packages/agent-control-specification/) · [gitagent](https://github.com/open-gitagent/gitagent) · [Interop protocols survey arXiv:2505.02279](https://arxiv.org/abs/2505.02279)
**Self-evolution content:** [Self-Evolving Agents survey arXiv:2507.21046](https://arxiv.org/html/2507.21046v4) · [Agentic Adaptation survey arXiv:2512.16301](https://arxiv.org/pdf/2512.16301) · [Memory survey arXiv:2512.13564](https://arxiv.org/pdf/2512.13564) · [ReCreate arXiv:2601.11100](https://arxiv.org/pdf/2601.11100) · [Voyager overview](https://beancount.io/bean-labs/research-logs/2026/05/08/voyager-open-ended-embodied-agent-lifelong-learning)
**Safety/governance:** [MIT AI Agent Index 2025](https://aiagentindex.mit.edu/data/2025-AI-Agent-Index.pdf) · [Policy-as-Prompt arXiv:2509.23994](https://arxiv.org/abs/2509.23994) · [AgentGuardian arXiv:2601.10440](https://arxiv.org/pdf/2601.10440) · [AgentDoG arXiv:2601.18491](https://arxiv.org/pdf/2601.18491) · [Rethinking Autonomy arXiv:2508.11824](https://arxiv.org/pdf/2508.11824)

## Methodology
14 live web searches (June 12, 2026) + 2 full-text deep reads (Externalization review; Open Agent Spec TR) across 5 sub-questions: (1) harness-evolution lineage and baselines, (2) scaffold-search prior art, (3) component taxonomies incl. ETCLOVG verification, (4) declarative agent specification standards, (5) self-evolving skills/memory and governance. All papers named in the report were located at their primary source (arXiv/OpenReview/official docs). Single-source items (HarnessForge, Runtime Harness Adaptation, Harness Flaws, AgentFactory, ReCreate — all very recent) are cited by arXiv ID but summarized only from abstracts/search context; flag for full reads before final citation in the paper.
