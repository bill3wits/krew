# Crew voices vs the owner's identity

Two different filesystems. Do not put character clones in a people store.

| What | Where | Ships with |
|------|-------|------------|
| Speech **clones** (one wav per crew voice: `crissa`, `bash`, `announcer`, ...) | the voice module's clip folder under the Crissa state home | the Crissa voice module (this plugin talks HTTP to its tiers) |
| Ear **veto prints** (speaker embeddings of those mouths so the ear does not stamp Crissa as a person) | the voice module's own folder, never the owner's identity store | the Crissa voice module |
| The crew's **PA** | the announcer tier (Audio8 on the CPU), through the Wall's player `/v1/announce` | `team_announce` |
| The owner's enrollment wavs | Crissa's Init, in its identity store | Init on that machine; never copied by krew |

`krew-tts` does not vendor wavs. A new machine speaks with each tier's built-in voice until the owner records
clips through Crissa's Init; krew never copies a clone into an identity store.
