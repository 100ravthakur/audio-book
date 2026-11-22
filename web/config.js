// Where the AudioReader backend lives.
//
// Served by the backend itself (local, or the Render service): leave it empty,
// the page talks to its own origin.
//
// Deployed to Vercel as a static site: set this to your Render URL, e.g.
//   window.AUDIOREADER_API = "https://audioreader-xxxx.onrender.com";
//
// You can also set it at runtime from the page itself — click "backend" under
// the URL box — which stores it in this browser only.
window.AUDIOREADER_API = "https://audioreader.onrender.com";

// Only if you set APP_TOKEN on the backend.
window.AUDIOREADER_TOKEN = "";
