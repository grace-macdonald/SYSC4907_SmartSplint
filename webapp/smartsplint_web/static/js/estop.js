// App emergency stop (section 1.7): sends a stop command to the brace over Web Bluetooth,
// then logs the press on the server. The proposal treats this as a convenience; the
// latching hardware button on the brace is the safety stop, and the UI says so.
//
// The GATT service / characteristic UUIDs come from server config
// (SMARTSPLINT_BLE_SERVICE_UUID, SMARTSPLINT_BLE_ESTOP_CHAR_UUID) and must match the firmware.
// Web Bluetooth needs Chrome or Edge (desktop or Android) and HTTPS (or localhost).

import { api, toast } from "./core.js";

const ESTOP_COMMAND = new Uint8Array([0x01]);
let cachedChar = null;

async function getCharacteristic(serviceUuid, charUuid) {
  if (cachedChar?.service?.device?.gatt?.connected) return cachedChar;
  const device = await navigator.bluetooth.requestDevice({ filters: [{ services: [serviceUuid] }] });
  const server = await device.gatt.connect();
  const service = await server.getPrimaryService(serviceUuid);
  cachedChar = await service.getCharacteristic(charUuid);
  device.addEventListener("gattserverdisconnected", () => { cachedChar = null; });
  return cachedChar;
}

async function sendStop() {
  const { bleService, bleChar } = document.body.dataset;
  if (!bleService || !bleChar) return { delivered: false, error: "Bluetooth stop is not configured on this server yet." };
  if (!("bluetooth" in navigator)) return { delivered: false, error: "This browser can't use Bluetooth (use Chrome or Edge)." };
  try {
    const ch = await getCharacteristic(bleService.toLowerCase(), bleChar.toLowerCase());
    await ch.writeValueWithResponse(ESTOP_COMMAND);
    return { delivered: true };
  } catch (e) {
    cachedChar = null;
    return { delivered: false, error: e.message || String(e) };
  }
}

export function initEstop() {
  const pid = document.body.dataset.pid;
  for (const btn of document.querySelectorAll("[data-estop]")) {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      const result = await sendStop();
      if (result.delivered) {
        toast({ title: "Stop sent to the brace", message: "The motors are off and the ankle is free to move.", status: "critical", timeout: 0 });
      } else {
        toast({
          title: "Use the stop button on the brace",
          message: `The app could not reach the brace. ${result.error}`,
          status: "critical",
          timeout: 0,
        });
      }
      try {
        await api(`/api/participants/${pid}/estop`, { method: "POST", body: { ble_delivered: result.delivered, ble_error: result.error || null } });
      } catch (e) {
        console.warn("could not log e-stop", e);
      }
      btn.disabled = false;
    });
  }
}
