"""Cutting a meeting into time chunks: shared by every model's recursive (rolling) mode.

Recursive summarization = how a live summary is built: the meeting arrives in chunks, and after each chunk the
model turns (summary so far + the new chunk) into a new summary so far. Every model in run.py can do this
(--mode recursive, the default), so their scores are comparable with what the live bot shows.
"""


# Cut time-ordered lines into consecutive chunks of about `seconds` each (the last one can be shorter).
def make_chunks(lines, seconds):
    chunks, current, start = [], [], None
    for line in lines:
        start = line.start if start is None else start
        if line.start - start >= seconds and current:
            chunks.append(current)
            current, start = [], line.start
        current.append(line)
    return chunks + ([current] if current else [])
