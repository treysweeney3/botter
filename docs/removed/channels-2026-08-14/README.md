# Channels — removed 2026-08-14

Botter managed Hermes messaging platforms (Telegram, Discord, Matrix, …) on the
**main** profile. Main is the user's own Slack agent and is never treated as a
bot, so this surface configured something Botter does not own. At removal, 0 of
27 platforms were enabled or configured.

Hermes' own dashboard still configures these: `hermes serve`, then
`GET/PUT /api/messaging/platforms/{id}`. Nothing was lost, only relocated to the
tool whose job it is.

Removed together with these files:

- `backend/botterd/channels.py` (kept here)
- `backend/tests/test_channels.py` (kept here)
- `Channel`, `ChannelEnvVar`, `ChannelUpdate`, `ChannelsResponse`, `ChannelResponse` in `botterd/models.py`
- `GET /v1/channels`, `PUT /v1/channels/{id}` in `botterd/main.py` and `mockserver/main.py`
- `channel_updated` from `ALLOWED_EVENTS`
- `ChannelRow`, `ChannelConfigSheet` in `app/Botter/Connections/ConnectionsSheet.swift`
- `Channel`, `ChannelEnvVar` and the channel client/store members in BotterKit

To restore, put the two files back and re-add the models, routes, event, and
views. `hermes_serve.py` is unchanged — the credential surface still uses it.
