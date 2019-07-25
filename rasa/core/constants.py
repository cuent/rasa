DEFAULT_SERVER_PORT = 5005

DEFAULT_SERVER_FORMAT = "http://localhost:{}"

DEFAULT_SERVER_URL = DEFAULT_SERVER_FORMAT.format(DEFAULT_SERVER_PORT)

DEFAULT_NLU_FALLBACK_THRESHOLD = 0.0

DEFAULT_CORE_FALLBACK_THRESHOLD = 0.0

DEFAULT_FALLBACK_ACTION = "action_default_fallback"

DEFAULT_REQUEST_TIMEOUT = 60 * 5  # 5 minutes

DEFAULT_LOCK_LIFETIME = 60

REQUESTED_SLOT = "requested_slot"

# start of special user message section
INTENT_MESSAGE_PREFIX = "/"

USER_INTENT_RESTART = "restart"

USER_INTENT_BACK = "back"

USER_INTENT_OUT_OF_SCOPE = "out_of_scope"

ACTION_NAME_SENDER_ID_CONNECTOR_STR = "__sender_id:"
