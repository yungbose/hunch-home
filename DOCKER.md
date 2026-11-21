# Hunch Home in Docker

On Home Assistant Container, run the same image beside Home Assistant on the same machine, with an access token.
You need Docker with its Compose plug-in (`docker compose`). To build the image yourself you also need its
buildx plug-in: the build reads the model from a second build context, which Docker refuses without it.
[compose.example.yaml](compose.example.yaml) has the whole recipe; in short:

1. In Home Assistant, make a long-lived access token: your profile, **Security**, **Long-lived access tokens**,
   **Create token**. Put it in a file named `.env` beside the compose file, readable by you only:
   `HASS_TOKEN=paste-the-token-here`.
2. Add the `hunch` service from the example to your compose file (image `ghcr.io/yungbose/hunch-home:0.2.0`) and
   run `docker compose up -d`.
3. In Home Assistant: **Settings > Devices & services > Add integration**, **Wyoming Protocol**, host
   `127.0.0.1`, port `10500`.
4. Open the page at http://127.0.0.1:8099/ on that machine and press **Add Hunch to my assistant**.

Then carry on from [Try it](README.md#try-it) in the read-me.

## Home Assistant on another network or machine

`127.0.0.1` is right only when Home Assistant runs on this machine's own network, as in the example (both
containers with `network_mode: host`). If Home Assistant runs on a network of its own, or on another machine:

- set `HUNCH_WYOMING_HOST` to an address of this machine that Home Assistant can reach;
- set `HUNCH_ALLOW_FROM` to Home Assistant's address as the container sees it (the app refuses to start without
  it, since the Wyoming port has no sign-in);
- set `HASS_URL` to Home Assistant's address, and give the Wyoming integration this machine's address as host.

Both of Hunch's ports stay on the machine: the Wyoming port (10500) has no sign-in, and the page (8099) has no
sign-in of its own. Never publish either on your network. To open the page from another computer, set
`HUNCH_PAGE_HOST` and `HUNCH_PAGE_ALLOW_FROM` (below).

## Settings

[compose.example.yaml](compose.example.yaml) sets the usual ones. The others:

| Variable | Default | Meaning |
|---|---|---|
| `HASS_URL`, `HASS_TOKEN` | none | Home Assistant's address and a long-lived access token. |
| `HUNCH_WYOMING_HOST` | `127.0.0.1` | Where the Wyoming port listens. Anything else needs `HUNCH_ALLOW_FROM`. |
| `HUNCH_ALLOW_FROM` | this machine | The addresses the Wyoming port answers, comma separated. |
| `HUNCH_PAGE_HOST` | `127.0.0.1` | Where the page listens. Anything else needs `HUNCH_PAGE_ALLOW_FROM`. |
| `HUNCH_PAGE_ALLOW_FROM` | none | The addresses that may open the page, comma separated. |
| `HUNCH_PAGE_HOSTS` | none | More names the page may be opened by. |
| `HUNCH_THREADS` | `2` | Processor threads for the model. |
| `HUNCH_LOG` | `info` | `debug` adds detail, which can include sentences and device names. |
| `HUNCH_HEALTH_FILE` | `/tmp/hunch-home.alive` | The file the app touches while it serves, which the image's health check reads. In a read-only container its folder must be writable (the example gives `/tmp` its own small space). |

The app refuses to start if a port is opened to the network without its list of allowed addresses. Every change
the page makes must come from the page itself, which another website cannot imitate.

## Listening in Docker

Listening with no wake word works in Docker too, with the microphone given to the container:
[Listening with no wake word](REFERENCE.md#switching-on-listening) has the steps.

## Removing it, and what has been tried

Follow [Removing it](README.md#removing-it) in the read-me, then stop and remove the `hunch` container and its
volume, and delete the Wyoming Protocol entry you added under **Settings > Devices & services**. What has been
tried in Docker is under
[What has been tried](REFERENCE.md#what-has-been-tried-and-what-has-not).
