// Doodle Arena web config

// Twitch channel shown on stream.html
export const TWITCH_CHANNEL = "wetsandwiches03102026";

// Firebase web config (Firebase console -> Project settings -> Your apps -> Web app).
// Leave as null for DRY-RUN mode: "Create" logs the JSON to the console instead of
// writing to Firestore, so you can test the page before Firebase is set up.
// This config is not secret; it is meant to live in the page.
export const firebaseConfig = {
  apiKey: "AIzaSyAKgGYWKKidaG8le8AdbP4qGVTget-ZFPg",
  authDomain: "wet-sandwich-d7ba3.firebaseapp.com",
  projectId: "wet-sandwich-d7ba3",
  storageBucket: "wet-sandwich-d7ba3.firebasestorage.app",
  messagingSenderId: "924645082894",
  appId: "1:924645082894:web:ecff7eb623f2c53dd8f95f",
  measurementId: "G-XXQRWDPEJ2"
};
/* Example:
export const firebaseConfig = {
  apiKey: "...",
  authDomain: "your-project.firebaseapp.com",
  projectId: "your-project",
  storageBucket: "your-project.appspot.com",
  messagingSenderId: "...",
  appId: "..."
};
*/
