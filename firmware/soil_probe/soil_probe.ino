// soil_probe.ino — 阶段0探针：实测 5 路 TLC555 土壤传感器方波频率
// 用法：接好线 -> Arduino IDE 选 ESP32 Dev Module -> 烧录 -> 打开串口监视器 115200
// 目的：确认每路传感器输出频率范围 + 干/湿变化方向，之后据此定测频方案和校准

const int SENSOR_PINS[5] = {32, 33, 25, 26, 27}; // 5 路 OUT 接入的 GPIO

void setup() {
  Serial.begin(115200);
  for (int i = 0; i < 5; i++) {
    pinMode(SENSOR_PINS[i], INPUT);
  }
  Serial.println("=== Soil sensor frequency probe ===");
  Serial.println("Pins: 32 33 25 26 27");
  Serial.println("Ready. Expect: dry -> high freq, wet -> low freq");
}

// 用 pulseIn 测一个完整周期（高+低电平时间），换算频率。
// 频率太高（>几百kHz）时 pulseIn 会不准甚至超时，届时需改用 PCNT。
float measureFrequency(int pin) {
  unsigned long t_high = pulseIn(pin, HIGH, 2000000); // 2s 超时
  unsigned long t_low  = pulseIn(pin, LOW,  2000000);
  if (t_high == 0 || t_low == 0) return -1.0f;        // 无信号
  return 1000000.0f / (float)(t_high + t_low);        // Hz
}

void loop() {
  static unsigned long last = 0;
  if (millis() - last < 2000) return; // 每 2 秒刷新一组
  last = millis();

  for (int i = 0; i < 5; i++) {
    float f = measureFrequency(SENSOR_PINS[i]);
    Serial.printf("S%d (GPIO%d): ", i + 1, SENSOR_PINS[i]);
    if (f < 0) {
      Serial.println("no signal");
    } else {
      Serial.printf("%.0f Hz\n", f);
    }
  }
  Serial.println("---");
}
