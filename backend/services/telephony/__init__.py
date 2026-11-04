"""Phone provider: Vobiz.

`base` defines what a provider is (its account, how it places a call, how it reads a refusal) and what its audio WebSocket
looks like (`StreamCodec`); `vobiz` implements them; `registry` says whether it is set up. The call itself (voice activity,
turns, barge-in) is routers/phone.py, which speaks to the carrier only through a codec."""
