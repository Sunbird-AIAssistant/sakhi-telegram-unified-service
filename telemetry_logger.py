import requests
import time
import os
import uuid
from logger import logger
from config_util import get_config_value

telemetryURL = get_config_value('telemetry', 'TELEMETRY_ENDPOINT_URL', None)
ENV_NAME = get_config_value('telemetry', 'SERVICE_ENVIRONMENT', None)
TELEMETRY_LOG_ENABLED = get_config_value('telemetry', 'TELEMETRY_LOG_ENABLED', None).lower() == "true"
telemetry_id = get_config_value('telemetry', 'service_id', None)
telemetry_ver = get_config_value('telemetry', 'service_ver', None)
actor_id = get_config_value('telemetry', 'actor_id', None)
channel = get_config_value('telemetry', 'channel', None)
pdata_id = get_config_value('telemetry', 'pdata_id', None)
events_threshold = get_config_value('telemetry', 'events_threshold', None)

class TelemetryLogger:
    """
    A class to capture and send telemetry logs using the requests library with threshold limit.
    """

    def __init__(self, url=telemetryURL, threshold=int(events_threshold)):
        self.url = url
        self.events = []  # Store multiple events before exceeding threshold
        self.threshold = threshold

    def add_event(self, event):
        """
        Adds a telemetry event to the log.

        **kwargs:** Keyword arguments containing the event data.
        """
        
        logger.info(f"Telemetry event: {event}")
        
        if not TELEMETRY_LOG_ENABLED:
            return
        
        self.events.append(event)   
        # Send logs if exceeding threshold
        if len(self.events) >= self.threshold:
            self.send_logs()

    def send_logs(self):
        """
        Sends the captured telemetry logs using the requests library.
        """
        try:
            data = {
                    "id": telemetry_id,
                    "ver": telemetry_ver,
                    "params": {"msgid": str(uuid.uuid4())},
                    "ets": int(time.time() * 1000),
                    "events": self.events
            }
            headers = {"Content-Type": "application/json"}
            response = requests.post(self.url + "/v1/telemetry", json=data, headers=headers)
            response.raise_for_status()
            logger.debug(f"Telemetry API request data: {data}")
            logger.info("Telemetry logs sent successfully!")
            # Reset captured events after sending
            self.events = []
        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending telemetry log: {e}", exc_info=True)    

    def prepare_interect_event(self, eventInput: dict, etype="TOUCH"):
        """
        Prepare a telemetry event dictionary with the specified values. 
        Args:
            eventInput: Event Input.
            etype: Event type (default: "api_access")

        Returns:
            A dictionary representing the telemetry event data.
        """
        data = {
            "eid": "INTERACT",
            "ets": int(time.time() * 1000),  # Current timestamp
            "ver": "3.1",  # Version
            "mid": f"INTERACT:{round(time.time())}",  # Unique message ID
            "actor": {
                "id": actor_id,
                "type": "System",
            },
            "context": {
                "channel": channel,
                 "pdata": {
                    "id": pdata_id,
                    "ver": "1.0",
                    "pid": "telegrambot"
                },
                "env": ENV_NAME
            },
            "edata": {
                "type": etype,
                "level": eventInput.get("subtype"),
                "id": eventInput.get("edataId") + "-sakhi",
                "pageid": eventInput.get("edataId") + "-sakhi",
                "extra": {
                    "pos": []
                }
            }
        }

        if eventInput.get("x-request-id", None):
            data["context"]["sid"] = eventInput.get("x-request-id")

        if eventInput.get("x-device-id", None):
            data["context"]["did"] = eventInput.get("x-device-id")

        eventCData = self.__getEventCData(eventInput)
        if eventCData:
            data["context"]["cdata"] = eventCData
        return data
    def __getEventCData(self, eventInput: dict):
        eventCData = []
        if eventInput.get("x-consumer-id", None) and eventInput.get("x-source", None):
            eventCData = [
                {
                    "id": eventInput.get("x-consumer-id"),
                    "type": "ConsumerId"
                },
                {
                    "id": eventInput.get("x-source"),
                    "type": "Source"
                }
            ]
        return eventCData

    def __getEventEDataParams(self, eventInput: dict):
        eventEDataParams = [
            {"method": str(eventInput.get("method"))},
            {"url": str(eventInput.get("url"))},
            {"status": eventInput.get("status_code")},
            {"duration": int(eventInput.get("duration"))}
        ]
        flattened_dict = self.__flatten_dict(eventInput.get("body", {}))
        for item in flattened_dict.items():
            eventEDataParams.append({item[0]: item[1]})
        return eventEDataParams

    def __flatten_dict(self, d, parent_key='', sep='_'):
        flattened = {}
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                flattened.update(self.__flatten_dict(v, new_key, sep=sep))
            else:
                flattened[new_key] = v
        return flattened