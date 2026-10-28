class SourceChannel:
    def __init__(self, name, channel_id, last_message_id=0):
        self._name = name
        self._channel_id = channel_id
        # A restored position avoids re-sending old messages after a restart.
        self._last_message_id = int(last_message_id or 0)

    def name(self):
        return self._name

    def channel_id(self):
        return self._channel_id

    def last_message_id(self):
        return self._last_message_id

    def set_last_message_id(self, message_id):
        self._last_message_id = message_id

    def __repr__(self):
        return f"{self._name} ({self._channel_id})"
