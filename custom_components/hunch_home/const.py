"""Names and defaults for the Hunch Home integration. Imports nothing from Home Assistant."""

DOMAIN = "hunch_home"
NAME = "Hunch Home"
# The plug-in's own version, as in manifest.json (a test keeps the two equal); counted in the daily summary.
PLUGIN_VERSION = "0.1.1"

CONF_URL = "url"
CONF_TIMEOUT = "timeout"
CONF_HA_FIRST = "ha_first"
CONF_LOCKS_AND_DOORS = "locks_and_doors"
# The Hunch server's shared secret, sent as `Authorization: Bearer <secret>` when set. Stored in the config
# entry, never logged and never put in diagnostics.
CONF_SECRET = "secret"
# Reconfigure only: a box that removes the saved secret (the form never shows the secret itself).
CONF_REMOVE_SECRET = "remove_secret"
# Options (Configure): the local record, and the choice to share daily counts.
CONF_RECORD = "record"
CONF_SHARE_COUNTS = "share_counts"

# The Hunch Home server in Docker on the same machine listens here. As a Home Assistant app its address is
# http://ea895109-hunch:8770; the README says what it needs to accept Home Assistant on another machine.
DEFAULT_URL = "http://127.0.0.1:8770"
# Where the server is published; shown in the set-up form.
SERVER_PAGE = "https://github.com/yungbose/hunch-home-server"
DEFAULT_TIMEOUT = 2.0  # seconds; a slower reply hands the sentence back to Home Assistant
MIN_TIMEOUT = 0.2
MAX_TIMEOUT = 30.0
# Let Home Assistant's own exact sentences answer first, and ask Hunch only when they do not match.
DEFAULT_HA_FIRST = True
# Locks and doors (experimental): unlocking, and opening and closing doors, gates, garage doors and motorised
# windows. On by default; switched off, Home Assistant's own sentences handle them.
DEFAULT_LOCKS_AND_DOORS = True
# The local record is on by default: it stays in this Home Assistant.
DEFAULT_RECORD = True

# Sharing daily counts (opt-in, no words). The choice appears under Configure because this address is set; it
# is off for each home until its owner turns it on, and nothing is sent before that. The address is on
# xevall.com, which passes each message on to the receiving end, so the
# receiving end can move without a new version of the plug-in. Emptied, the choice appears nowhere and nothing
# is ever sent. It names www because the bare xevall.com answers every request with a redirect there. A test
# fails while the address is set and PRIVACY_NOTICE is not an https address or DELETION_CONTACT is not an email
# address.
SHARE_ENDPOINT = "https://www.xevall.com/api/hunch-home/counts"
# Where people write about what was shared (xevall's published address, as in the model's NOTICE file).
DELETION_CONTACT = "privacy@xevall.com"
# The privacy notice's public address (https), shown with the choice to share: PRIVACY.md in the plug-in's
# own repository, published with it.
PRIVACY_NOTICE = "https://github.com/yungbose/hunch-home/blob/main/PRIVACY.md"

# Hunch Home reads English only.
LANGUAGES = ["en"]
