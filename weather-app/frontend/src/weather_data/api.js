import axios from "axios";

const API_BASE = "http://localhost:8000";

// send a post request to backend to fetch weather data
// wait for the response and return the data to the backend
// allows to get the dyanmic lat and lon from the user
export async function fetchWeather(lat, lon) {
  const res = await axios.post(`${API_BASE}/weather`, {
    lat: Number(lat),
    lon: Number(lon),
  });
  return res.data;
}

// triggers backend to (re)generate the text file and returns the file blob
// we set up a new blob (binary data from backend) so we allow it to create a file like object
// we then create a url for the blob to download the file every time
// we set up .click() to automatically download the file
export async function downloadTextFile(lat, lon) {
  const res = await axios.get(`${API_BASE}/download`, {
    params: { lat: Number(lat), lon: Number(lon) },
    responseType: "blob",
  });
  // create local download
  const url = window.URL.createObjectURL(new Blob([res.data]));
  const a = document.createElement("a");
  a.href = url;
  a.download = "today_weather.txt";
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}
