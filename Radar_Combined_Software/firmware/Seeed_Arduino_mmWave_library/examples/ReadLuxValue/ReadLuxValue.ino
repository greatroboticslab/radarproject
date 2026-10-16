#include <Arduino.h>
#include <hp_BH1750.h>  // Include the library for the BH1750 light sensor

// Create an instance of the BH1750 sensor object
hp_BH1750 BH1750;

void setup() {
  // Initialize the serial communication at a baud rate of 9600
  Serial.begin(9600);

  // Initialize the BH1750 sensor
  bool avail = BH1750.begin(BH1750_TO_GROUND);
  // BH1750_TO_GROUND or BH1750_TO_VCC should be used depending on how the
  // address pin of the sensor is wired This function will return false if no
  // sensor is found or if it's already connected to I2C

  // Check if the sensor is available
  if (!avail) {
    Serial.println("No BH1750 sensor found!");  // Print an error message if the
                                                // sensor is not found
    while (true) {
    };  // Loop indefinitely if the sensor is not found
  }

  // Uncomment the following line if you want to speed up your sensor
  // BH1750.calibrateTiming();

  // Print the conversion time of the sensor
  Serial.printf("conversion time: %dms\n", BH1750.getMtregTime());

  // Start the first measurement in setup
  BH1750.start();
}

void loop() {
  // Check if the sensor has a new value available
  if (BH1750.hasValue() == true) {
    // Get the current lux value from the sensor
    float lux = BH1750.getLux();
    Serial.println(lux);  // Print the lux value to the serial console

    // Start the next measurement
    BH1750.start();
  }

  // You can add other code here that will run concurrently with the sensor
  // measurements
  //...
}