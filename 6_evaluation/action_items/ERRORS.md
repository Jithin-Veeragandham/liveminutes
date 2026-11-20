# Action items: errors read by hand

20 random predictions per baseline, read with their transcript lines and the match result. Taken from the **dev** splits (AMI dev + synthetic dev together), so that the fixes made after reading them are dev-only changes. To get the same sample:

```bash
.venv/bin/python 6_evaluation/action_items/sample_errors.py --baseline rules --split dev --n 20
.venv/bin/python 6_evaluation/action_items/sample_errors.py --baseline zeroshot --split dev --n 20
```

(`numpy.random.default_rng(0)` over all dev predictions; final settings of the commit these notes are in.)

## Rules (249 dev predictions; 20 read)

| What happened | n | Examples |
|---|---|---|
| **Right task, right owner** (matched) | 9 | "Helen, could you put together the pitch for the library authorities" → Helen. "I'll put that on Tomasz. He can look at RFID pads versus barcode scanners" → Tomasz. "Gareth, for you, could you work out how we'd evaluate the final design?" → Gareth. |
| **Done in the meeting, or a question, not a task** | 4 | "Could you maybe draw something?" (the whiteboard exercise), "Suppose I can draw an animal" (AMI's kickoff ice-breaker), "Should I bring my pen too?", "Can you read it from across the kitchen though?" (a question about the product). A commitment phrase plus a work verb, but nothing to do after the meeting. |
| **A real task the gold doesn't list** (counted as a false positive) | 4 | "I will send you a mail", "I'll check what they exact mean", "Rafael, can you check a magnet's strong enough to hold it" (a sub-step of a larger gold item). One is a near miss: "Look into setting up a few quick tests" vs gold "Test the interface mock-ups with seniors", similarity 0.69, just under the 0.70 threshold. |
| **Meeting process** | 2 | "You'll each work on your own bit and get emails from the coach" (AMI's scenario instructions), "you're gonna look a bit more at the physical make up" (AMI; plausible, but the summary doesn't list it). |
| **Text without its content** | 1 | "Add it to his list" for a task to the absent person: the owner is right (Rafael, named in the line before), but the task itself was said earlier, so the text can't match. |

**Patterns across the dev runs (not only the 20):**
- **AMI recall is low because tasks are said indirectly.** The project manager lists them by role: "The Industrial Designer," on one line, "the components design" on the next; or "Um you're gonna look at product evaluation" with no name. Only 16 of the 41 AMI dev items get matched.
- **AMI gold is coarse.** One summary sentence covers a whole discussion ("The Designers and the Marketing Expert will work on their individual presentations"), while the rules find the small concrete promises made along the way. So AMI precision is low (about 0.15) even when the predictions are reasonable.
- **Owners of unnamed requests.** "Can you ...?" with no name goes to whoever answers or speaks next. That's right in two-person exchanges but often wrong when the project manager addresses the whole team.
- **Reassigned and dropped tasks** (synthetic edge cases): a later "Oh, I can take that, actually" or "So Marcus gets the usage numbers" now moves the task. A drop said without repeating the task's words ("let's leave that for now") is still missed, and the dropped task is predicted.

**Fixed after reading (all on dev, each checked on both dev sets):**
- "you can / you could / you have to": the generic "you" of a design idea, not a request ("You can make them red").
- "we have to make the buttons bigger": "we" / "let's" now need a verb of work done after the meeting.
- Future assignments with in-meeting verbs: "you're gonna look at product evaluation".
- Owner from "put that on Tom", "ask Priya to", "add it to his list" (named before, or the one absent person), "Marcus gets ...", "Oh, I can take that, actually".
- Unnamed requests go to the next speaker when nobody answers "sure".
- Item text cut at the end of the sentence, unless the task is in the next one ("Can you find out for sure? Whether the big catalogue vendors all support it?").
- Two different tasks of the same person on nearby lines were merged into one.

## Zero-shot (157 dev predictions; 20 read)

| What happened | n | Examples |
|---|---|---|
| **Right task** (matched) | 7 | "Tomasz, for next time, can you look at components? RFID pad, barcode scanner, printer" → Tomasz. "I'll write up the minutes and the final report for management" → Helen. "Since he's not here, I'm gonna give him the market research" → the absent marketing expert. **Owner wrong in 2**: "you're gonna look at product evaluation" (no name, nobody answers: unassigned, gold = Marketing Expert), and #6 below. |
| ...of which **a lucky match** | 1 | "Let's proceed with this model, okay, for the marketing direction" matched "The Marketing Expert will begin his marketing plan" (similarity 0.74). The embedding match rewards shared topic words; one in seven matches read here was not really the same task. |
| **Not a task at all** | 10 | Design ideas ("you'll need the advanced chip for the LCD", "we can have a tie-up with who do the fabric"), the agenda ("we're gonna do some tool training, project plan and discuss"), an introduction ("I'm Marcus, I'll be project manager on this one"), how the product works ("you put the books on the pad"), opinions ("I'm fine with wristband"), past work ("I was working on a schedule"), design questions ("could we drop the fan?"). The classifier reacts to "I'll", "we're gonna", "you'll", not to whether there is work to do after the meeting. |
| **A real task, text without its content** | 3 | "Chase them this week", "Okay, so the straps go to Marcus then", "I'll look at what we can get inside twelve fifty": real commitments, but the line alone doesn't say what the task is, so the text can't match the gold item. |

**Patterns across the dev runs:**
- **The scores don't separate tasks from talk.** With one hypothesis, the pipeline's score is entailment vs contradiction only; it is above 0.9 for 81% of AMI dev lines, so the dev-chosen threshold is 0.9994 and small changes in wording move many lines across it. The two-label mode (task vs "small talk, a question or a discussion") didn't do better on dev (best mean F1 0.27 vs 0.33).
- **Recall is low on synthetic** (0.33 on dev): at a threshold that strict, most plainly worded tasks ("I'll email the client", "Priya, can you send ...") are below it. The rules find these.
- **Owner accuracy is high when it matches** (0.87 synthetic dev), because the owner comes from the rules, and the lines that pass the threshold are mostly explicit ones ("Tomasz, can you ...").

**Fixed after reading (dev):** fragments with fewer than 2 content words are no longer scored ("Okay so this we had, this we had.", "And to, okay and back, also.", "You go ahead."). In the first dev sample, 8 of 20 predictions were such fragments. Dev mean F1 went from 0.29 to 0.33, with the same wording, context and threshold chosen again.

## Both: what would help

- **AMI's tasks are said indirectly and summarized coarsely.** A system that reads the whole closing part of the meeting ("so for next time: the industrial designer, the components concept; the user interface designer, ...") and writes one item per role would fit AMI's gold much better than line-by-line rules or classification. That's what the LLM should do.
- **Dropped and reassigned tasks need the whole meeting.** "Let's leave that one for now" without the task's words, or a reassignment several minutes later, needs memory of all open tasks.
- **Matching:** the 0.70 cosine threshold gives some lucky matches and misses some paraphrases (0.69). Reading the matched pairs in `results/runs/<run_id>.json` is the way to check a surprising number.

## Not changed after seeing eval / test (reported only)

- **AMI eval is much lower than AMI dev** (rules F1 0.08 vs 0.21, zero-shot 0.09 vs 0.23). 3 of the 20 eval meetings have no gold action at all, and most eval actions are role-level summaries ("The Industrial Deigner will prepare the working design"). Nothing was tuned on it.
- **Gold owners in IS1009** use "interface specialist" and "marketing manager"; `ami_owners` reads "marketing manager" as Marketing Expert + Project Manager and misses "interface specialist". Found while reading eval output. It only affects owner accuracy, and neither baseline matched an IS1009 item, so no reported number changes. Left as is, so the evaluation stays the one fixed before the eval runs; to fix later together with a new dev check.
