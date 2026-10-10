"""Where SellerGenome plugs into a LiveKit voice agent (sketch — fill in your STT/LLM/TTS).

The bot owns audio; SellerGenome owns the persona. Pull context before the first word, push every
turn, and re-pull at a turn boundary whenever the revision changes.
"""

from client import GenomeVoiceClient


async def entrypoint(ctx):  # livekit.agents JobContext
    genome = GenomeVoiceClient()
    genome.register(glid=ctx.job.metadata.get("glid"), provider="livekit", room=ctx.room.name)
    persona = genome.context()

    instructions = "\n\n".join([
        persona["prompt_sections"]["identity"], persona["prompt_sections"]["goal"],
        "Persona: " + (persona["prompt_sections"]["persona"] or ""),
        "Confirm first: " + "; ".join(persona["prompt_sections"]["to_confirm"]),
        "Never: " + "; ".join(persona["prompt_sections"]["guardrails"]),
    ])
    # session = AgentSession(stt=sarvam.STT(language=persona["voice"]["language"]),
    #                        llm=..., tts=sarvam.TTS(speaker=persona["voice"]["provider_voice"],
    #                                                pace=persona["voice"]["pace"],
    #                                                target_language_code=persona["voice"]["language"]))
    # await session.start(Agent(instructions=instructions), room=ctx.room)
    # await session.say(persona["teleprompter"]["opening"])
    #
    # @session.on("user_input_transcribed")  # final transcripts only
    # def on_user(ev):
    #     ack = genome.events({"type": "transcript", "speaker": "seller", "text": ev.transcript})
    #     if genome.changed(ack):                      # e.g. seller asked for Hindi
    #         new = genome.context()
    #         session.update_options(...)              # apply language / pace / voice at the turn boundary
    #
    # @session.on("agent_speech_committed")
    # def on_bot(ev): genome.events({"type": "transcript", "speaker": "bot", "text": ev.text})
    #
    # On hang-up: genome.events({"type": "outcome", "label": "..."}, {"type": "ended", "reason": "hangup"})
    return instructions
