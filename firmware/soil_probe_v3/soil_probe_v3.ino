// soil_probe_v3.ino — 5 路 AOUT 土壤传感器模拟读取
// 模块输出直流电压（AOUT）：干燥时电压高，湿润时电压低
const int PINS[5] = {32, 33, 25, 26, 27}; // 均为 ADC1 通道

void setup() {
  Serial.begin(115200);
  for (int i = 0; i < 5; i++) {
    pinMode(PINS[i], INPUT);
    analogSetPinAttenuation(PINS[i], ADC_11db); // 0~3.3V 满量程
  }
  Serial.println("=== probe v3: 5x analog soil moisture ===");
}

void loop() {
  static unsigned long last = 0;
  if (millis() - last < 2000) return;
  last = millis();
  for (int i = 0; i < 5; i++) {
    int adc = analogRead(PINS[i]);
    float v = adc / 4095.0f * 3.3f;
    Serial.printf("S%d (GPIO%d): ADC=%d  %.2fV\n", i + 1, PINS[i], adc, v);
  }
  Serial.println("---");
}
