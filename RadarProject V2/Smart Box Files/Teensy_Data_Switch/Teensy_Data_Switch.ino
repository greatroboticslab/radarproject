#include <QNEthernet.h>
using namespace qindesign::network;

EthernetClient client;
IPAddress serverIP(192, 168, 1, 10); // Cognex camera IP
const uint16_t serverPort = 5000;   // Cognex camera TCP port

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 5000);

  // Start Ethernet
  Ethernet.begin();
  Serial.println(Ethernet.localIP());

  // Connect to Cognex
  if (client.connect(serverIP, serverPort)) {
    Serial.println("Connected to Cognex!");
  } else {
    Serial.println("Connection failed.");
  }
}

void loop() {
  // Example: tell Cognex to send location data
  sendModeToCognex(1);
  delay(2000);

  // Example: tell Cognex to send angle data
  sendModeToCognex(2);
  delay(2000);
}

void sendModeToCognex(int mode) {
  if (client.connected()) {
    client.print(mode);   // Send just "1" or "2"
    client.print('\n');   // Send newline to terminate
    Serial.print("Sent mode: ");
    Serial.println(mode);
  } else {
    Serial.println("Not connected.");
  }
}
