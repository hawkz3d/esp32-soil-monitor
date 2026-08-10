// soil_probe_v2.ino — 双测：ADC 电压 + 方波频率，判断传感器输出类型
// 只测 GPIO32。预期：
//   - 若 ADC 有明显读数且随干/湿变化 -> 模块是 AOUT 模拟输出，用 analogRead
//   - 若 freq 有读数 -> 模块是方波输出，用 pulseIn/PCNT
const int PIN = 32;

void setup() {
  Serial.begin(115200);
  pinMode(PIN, INPUT);
  analogSetPinAttenuation(PIN, ADC_11db); // 满量程 0~3.3V
  Serial.println("=== probe v2: analog + freq (GPIO32) ===");
}

void loop() {
  static unsigned long last = 0;
  if (millis() - last < 1000) return;
  last = millis();

  int adc = analogRead(PIN);                 // 0~4095
  unsigned long th = pulseIn(PIN, HIGH, 100000); // 0.1s 超时
  unsigned long tl = pulseIn(PIN, LOW, 100000);
  float f = (th > 0 && tl > 0) ? 1000000.0f / (th + tl) : 0;

  Serial.printf("ADC=%d  freq=%.0f Hz  %s\n", adc, f,
                f > 0 ? "(PULSE)" : "(DC/no pulse)");
}
