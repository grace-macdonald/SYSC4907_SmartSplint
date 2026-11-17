/*
  five_fsr_toe_walking.ino -- streams FIVE FSRs over serial and flags toe walking.
  Based on fsr_stream.ino (same board, wiring, 12-bit ADC and 200 Hz timing).
  Pair with plot_fsr.py on the computer.

  Sensor numbering (red numbers on the foot map):
    FSR1  big toe (hallux)
    FSR2  1st metatarsal head (medial forefoot)
    FSR3  2nd/3rd metatarsal head (central forefoot)
    FSR4  5th metatarsal head (lateral forefoot)
    FSR5  heel

  Output (one line per sample at SAMPLE_HZ):  t_us,fsr1,fsr2,fsr3,fsr4,fsr5
  Status lines start with '#', e.g.  # TOE WALKING DETECTED ...
  The '#' prefix lets plot_fsr.py tell status lines apart from data.

  Wiring (one voltage divider per FSR, 3.3V -- not 5V):
    3.3V ---- FSR ---+---- FSR_PINS[i]
                     |
                   10k ohm
                     |
                    GND

  Set FSR_PINS to the real ADC pin names you confirmed with the jumper test.
  Do NOT use PA2/PA3 -- they carry the serial link.

  Toe-walking check: every sample, a switch-case looks at whether FSR4
  and/or FSR5 are in contact. At the end of each WINDOW_MS window, if the
  forefoot (FSR1-3) was loaded but FSR4 and FSR5 never touched down,
  the sketch prints a TOE WALKING flag.
*/

const int NUM_FSR = 5;
//                              FSR1  FSR2  FSR3  FSR4  FSR5
const int FSR_PINS[NUM_FSR] = { PA0,  PA1,  PA4,  PA6,  PA7 };   // <-- EDIT to your working ADC pins

const unsigned long SAMPLE_HZ = 20;
const unsigned long PERIOD_US = 1000000UL / SAMPLE_HZ;
const unsigned long WINDOW_MS = 5000;   // collection window for the toe-walking check
const int HEEL = 4;   // index of FSR5. Arrays count from 0, so sensor 5 is index 4 (contact[5] does not exist)

const int CONTACT_ON  = 480;   // rise above this -> contact
const int CONTACT_OFF = 320;   // fall below this -> released

// Ground contact pattern on this sample, for the switch-case.
enum FootContact {
  AIR          = 0,   // nothing loaded
  FOOT_HEEL_UP = 1,   // FSR1-4 loaded, heel up (push-off, or toe walking)
  HEEL_ONLY    = 2,   // heel strike
  FLAT         = 3    // heel and rest of the foot
};

unsigned long nextSample  = 0;
unsigned long windowStart = 0;
bool contact[NUM_FSR] = { false, false, false, false, false };
bool footSeen   = false;   // any sensor loaded this window
bool heelSeen   = false;   // FSR5 touched down this window
bool toeWalking = false;   // last window was flagged

void evaluateWindow() {
  if (!footSeen) {
    Serial.println("# WINDOW: no ground contact");
  } else if (!heelSeen) {
    Serial.println("# TOE WALKING DETECTED: sensor 5 made no ground contact this window");
    toeWalking = true;
  } else {
    Serial.println(toeWalking ? "# WINDOW: heel contact restored"
                              : "# WINDOW: normal contact pattern");
    toeWalking = false;
  }
  footSeen = false;
  heelSeen = false;
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);   // 0-4095
  delay(500);
  Serial.println("t_us,fsr1,fsr2,fsr3,fsr4,fsr5");
  nextSample = micros();
  windowStart = millis();
}

void loop() {
  unsigned long now = micros();
  if ((long)(now - nextSample) < 0) return;
  nextSample += PERIOD_US;

  // 1. Sample all five FSRs and stream them as one CSV line.
  Serial.print(now);
  for (int i = 0; i < NUM_FSR; i++) {
    int raw = analogRead(FSR_PINS[i]);
    if (!contact[i] && raw > CONTACT_ON)  contact[i] = true;
    if (contact[i]  && raw < CONTACT_OFF) contact[i] = false;
    Serial.print(',');
    Serial.print(raw);
  }
  Serial.println();

  // 2. Assess contact while collecting.
  bool rest = contact[0] || contact[1] || contact[2] || contact[3];
  int pattern = (contact[HEEL] ? HEEL_ONLY : 0) | (rest ? FOOT_HEEL_UP : 0);
  switch (pattern) {
    case AIR:
      break;
    case FOOT_HEEL_UP:
      footSeen = true;          // heel up: decide at the end of the window
      break;
    case HEEL_ONLY:
    case FLAT:
      footSeen = true;
      heelSeen = true;
      break;
  }

  // 3. End of window: flag toe walking if the heel never touched down.
  if (millis() - windowStart >= WINDOW_MS) {
    evaluateWindow();
    windowStart = millis();
  }
}