<!-- Verbatim copy of the hackathon problem statement (docs/inputs/problems/PS04_Persona_Design (1).docx.md). Immutable input. -->

**VOICE AI HACKATHON 2.0**

**Problem Statement 04**

**Persona Design**

*A persona built for each seller, that adapts during the call*

# **1\. Background**

VANI talks to every seller in one voice and one style, and keeps it for the whole call. Sellers differ in pace, patience, language and how they object. A seller who gets irritated, confused or rushed mid-call still gets exactly the same delivery.

Human executives adjust naturally, both before the call (based on who they are calling) and during it (based on how the conversation is going). This hackathon challenges participants to give VANI the same ability.

# **2\. Core Challenge**

Before each call, build the VANI persona that fits that seller from their seller data, and adapt it live as the call unfolds.

# **3\. Problem in Detail**

## **3.1 Seller Data as Input**

The persona starts from what is already known about the seller. Seller data is provided and includes:

* Profile and business type

* Turnover

* Categories

* Engagement

* Past call outcomes and objections

## **3.2 Persona Generator**

A generator that takes a seller's data and produces a persona specification for that seller. The persona sets:

* **Voice:** Gender, accent, pitch and pace.

* **Language:** Past call outcomes and objections

* Hinglish mix and formality.

* **Tone:** Warmth and empathy.

* **Reason for each choice:** Every setting in the persona spec comes with a short explanation tied to the seller data, so that a reviewer can see why it was chosen.

## **3.3 Conversation Plan**

Beyond voice and tone, each persona comes with a plan for how the call will run. Every element must be tied to seller data:

* Opening

* Personalisation

* Objection playbook

* Escalation rules

* **Guardrails:** Personas change delivery, never facts, pricing or compliance statements.

## **3.4 Evidence from Past Calls**

Persona choices should be backed by what has actually worked.

* Use transcripts, summaries and outcomes of past calls to show which persona choices work for which seller types.

* Link each major rule in the generator to this evidence (for example, sellers who object on price respond better to a particular opening and pace).

* Be clear about how strong the evidence is and where the generator is making an informed guess.

## **3.5 Live Adaptation**

The persona set before the call is a starting point. VANI must watch the conversation and change the persona mid-call when the seller's behaviour calls for it.

* Frustration

* Confusion

* Interest

* Language switch

* **Rush:** The seller wants the call to be quick.

* For each detected signal, VANI changes the relevant persona settings (for example, slows down and simplifies when confused, shortens and gets to the point when rushed, or switches language when the seller does).

* Every switch is written to a switch log: the signal detected, the change made and the reason.

## **3.6 Voice Demo on Sarvam**

The work must be shown in voice, not only on paper.

* Run the same scenario for 2 to 3 contrasting sellers, so the difference between their personas is clearly audible.

* Include at least one live mid-call switch.

* The voice should sound natural in at least 2 languages.

# **4\. Resources Provided**

* **Seller data:** Sample seller files with profile, business type, turnover, categories, engagement, and past call outcomes and objections. Provided in the shared folder. Link: \[to be shared\]

* **Past call material:** Transcripts, summaries and outcomes of past calls, for building evidence. Provided in the shared folder.

* **Voice platform:** The demo is built on Sarvam. Access details and any credits: \[To be confirmed by organisers\]

# **5\. Submission Deliverables**

1. **Persona generator running on sample seller files:** Working code that takes a seller file as input and outputs a persona.

2. **Persona spec per seller, with the reason for each choice:** One spec for every sample seller, covering voice, language, tone and conversation plan.

3. **Live demo with at least one mid-call switch:** A voice demo on Sarvam for 2 to 3 contrasting sellers.

4. **Switch log:** For every live switch: the signal detected, the change made and the reason.

5. **One-Slide Presentation:** A single slide with a sample persona spec, the past-call evidence behind it, and a high level workflow covering the chosen LLM, Sarvam, data flow, live signal detection and integration logic.

# **6\. Evaluation Criteria**

*Weightages are indicative and will be confirmed before the hackathon begins.*

| Criteria | Description | Weight |
| :---- | :---- | :---: |
| **Fit of each persona to its seller** | How well the persona's voice, language, tone and plan suit the seller's data. | 25% |
| **Correct, well-timed mid-call switches** | Whether signals are detected correctly and the change happens at the right moment. | 25% |
| **Distinctness of personas across seller types** | Whether different sellers get clearly different personas, not minor variations of one. | 20% |
| **Natural voice in 2+ languages** | Quality and naturalness of the voice demo across languages. | 15% |
| **Support from past-call evidence** | How well persona choices are backed by transcripts, summaries and outcomes. | 15% |

# **7\. FAQs**

**Q1: Do we need to build a fixed library of personas?**

**Answer:** No. The persona is generated for each seller from their data. You may group sellers into types to explain your logic, but the output is a persona per seller.

**Q2: Do we have to use Sarvam for the demo?**

**Answer:** Yes, the voice demo is expected on Sarvam. \[To be confirmed by organisers\] for access and credits.

**Q3: Does the mid-call switch have to be live?**

**Answer:** Yes. At least one switch must happen during a live demo call, and be recorded in the switch log. A pre-scripted switch that cannot respond to what the seller says will score lower.

**Q4: Which signals must we detect?**

**Answer:** Frustration, confusion, interest, a language switch and a rush. You may add others.

**Q5: Can a persona change what VANI says about product or price?**

**Answer:** No. Personas change delivery and approach only. Facts and compliance content stay the same.

**Q6: What if a seller file has missing fields?**

**Answer:** Fall back to sensible defaults, mark the affected choices as lower confidence in the spec, and let live adaptation correct them.