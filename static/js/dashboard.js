// dashboard.js - connects to server's /dashboard namespace and shows live data
const socket = io("/dashboard", {transports:["websocket","polling"]});

const deviceListEl = document.getElementById("device-list");
const liveFeedEl = document.getElementById("live-feed");
const pingSelect = document.getElementById("ping-device-select");
const pingBtn = document.getElementById("ping-btn");

let devices = {}; // device_id -> info
let rttChart = null;
let ingestChart = null;
let ingestEvents = [];

// helper
function el(html){ const d=document.createElement('div'); d.innerHTML = html.trim(); return d.firstChild; }

function renderDevices(){
  deviceListEl.innerHTML = "";
  pingSelect.innerHTML = "";
  Object.values(devices).sort((a,b)=> (b.last_seen||0)-(a.last_seen||0)).forEach(dev=>{
    const last_seen = dev.last_seen ? new Date(dev.last_seen).toLocaleString() : "-";
    const rtt = dev.rtt_ms == null ? "-" : dev.rtt_ms + " ms";
    const connected = dev.socket_connected ? "✅" : "🔌";
    const html = `
      <div class="p-2 border rounded flex justify-between items-center">
        <div>
          <div class="font-medium">${dev.name || dev.device_id}</div>
          <div class="text-xs text-gray-500">id: ${dev.device_id}</div>
          <div class="text-xs text-gray-500">last: ${last_seen}</div>
        </div>
        <div class="text-right text-sm">
          <div>${connected}</div>
          <div class="text-xs text-gray-500">${rtt}</div>
        </div>
      </div>`;
    deviceListEl.appendChild(el(html));
    const opt = document.createElement("option"); opt.value = dev.device_id; opt.text = dev.name || dev.device_id;
    pingSelect.appendChild(opt);
  });
}

// live feed append
function addFeed(msg){
  const row = document.createElement("div");
  row.className = "p-2 border-b text-xs";
  row.innerText = `[${new Date(msg.recv_ts || Date.now()).toLocaleTimeString()}] ${msg.device_name || msg.device_id} → ${JSON.stringify(msg.payload)}`;
  liveFeedEl.prepend(row);
  // track ingest rate
  ingestEvents.push(Date.now());
  pruneIngestEvents();
  updateIngestChart();
}

function pruneIngestEvents(){
  const since = Date.now() - 60_000; // last 60s
  ingestEvents = ingestEvents.filter(t=>t>=since);
}

function updateIngestChart(){
  if(!ingestChart) return;
  ingestChart.data.datasets[0].data[0] = ingestEvents.length;
  ingestChart.update();
}

// RTT chart updates
function updateRttChart(){
  if(!rttChart) return;
  const labels = Object.values(devices).slice(0,10).map(d=>d.name||d.device_id);
  const data = Object.values(devices).slice(0,10).map(d=>d.rtt_ms||0);
  rttChart.data.labels = labels;
  rttChart.data.datasets[0].data = data;
  rttChart.update();
}

// Socket handlers
socket.on("connect", ()=>{ console.log("dashboard connected"); });
socket.on("snapshot", data=>{
  (data.devices||[]).forEach(d=> devices[d.device_id]=d);
  renderDevices(); updateRttChart();
});
socket.on("device_update", d=>{
  devices[d.device_id] = {...(devices[d.device_id]||{}), ...d};
  renderDevices(); updateRttChart();
});
socket.on("ingest", data=>{
  // update device quick info
  const id = data.device_id;
  devices[id] = {...(devices[id]||{}), device_id:id, name:data.device_name, last_seen:data.recv_ts, last_payload:data.payload, last_device_ts:data.device_ts};
  renderDevices();
  addFeed(data);
  updateRttChart();
});
socket.on("ping_result", data=>{
  addFeed({recv_ts: data.recv_ts || Date.now(), device_name: data.device_id, payload: {ping_result: data}});
  if(data.rtt_ms){
    devices[data.device_id] = {...(devices[data.device_id]||{}), rtt_ms: data.rtt_ms};
    renderDevices(); updateRttChart();
  }
});

// setup charts
window.addEventListener("load", ()=>{
  const ctx = document.getElementById("rttChart").getContext("2d");
  rttChart = new Chart(ctx, {
    type: "bar",
    data: { labels: [], datasets: [{ label: "RTT (ms)", data: [] }] },
    options: { responsive:true, scales:{ y:{ beginAtZero:true } } }
  });
  const ctx2 = document.getElementById("ingestRateChart").getContext("2d");
  ingestChart = new Chart(ctx2, {
    type: "doughnut", data: { labels:["events_last_60s"], datasets:[{ data:[0] }]}, options:{responsive:true}
  });
});

// ping button
pingBtn.addEventListener("click", ()=>{
  const device_id = pingSelect.value;
  if(!device_id) return alert("Select a device to ping");
  const ping_id = ""+Date.now();
  socket.emit("ping_device", {device_id, ping_id});
});
