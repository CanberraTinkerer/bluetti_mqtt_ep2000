from setuptools import setup, find_packages

setup(
    name="bluetti-mqtt-ep2000",
    version="0.1.0",
    description="MQTT interface + logger for Bluetti power stations (EP2000/EP760 fork)",
    author="Alan (CanberraTinkerer)",
    license="MIT",
    packages=find_packages(),
    python_requires=">=3.7",
    install_requires=[
        "paho-mqtt<2.0",
        "bleak>=0.20.0",
    ],
    entry_points={
        "console_scripts": [
            "bluetti-mqtt = bluetti_mqtt.server_cli:main",
            "bluetti-logger = bluetti_mqtt.logger_cli:main",
            "bluetti-discovery = bluetti_mqtt.discovery_cli:main",
        ]
    },
)
