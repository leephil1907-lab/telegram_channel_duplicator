class MessagePreparer:
    def __init__(self, config):
        self.config = config

    def prepare(self, message):
        pass

    def check_whitelist(self, message, whitelist):
        # Media-only posts have message.message == None; treat them as empty text
        # instead of raising AttributeError (upstream bug).
        if not whitelist:
            return True

        text = (getattr(message, "message", None) or "").lower()
        return any(str(word).lower() in text for word in whitelist)
