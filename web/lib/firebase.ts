import { initializeApp, getApps, type FirebaseApp } from "firebase/app";
import { getAuth, GoogleAuthProvider, type Auth } from "firebase/auth";

const firebaseConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
};

/** Local testing without Google login. Backend already allows anonymous chat in development. */
export const authBypassEnabled =
  process.env.NEXT_PUBLIC_AUTH_BYPASS === "true" ||
  process.env.NEXT_PUBLIC_AUTH_BYPASS === "1";

export const firebaseConfigured = Boolean(
  firebaseConfig.apiKey &&
    firebaseConfig.authDomain &&
    firebaseConfig.projectId &&
    firebaseConfig.appId
);

function getFirebaseApp(): FirebaseApp | null {
  if (!firebaseConfigured) {
    return null;
  }
  return getApps().length === 0 ? initializeApp(firebaseConfig) : getApps()[0];
}

const app = getFirebaseApp();

// Next prerenders client components on the server. Firebase Auth must only be
// constructed in the browser, otherwise a missing/invalid build-time public
// key turns a static build into a hard prerender failure.
export const auth: Auth | null =
  typeof window === "undefined" || !app ? null : getAuth(app);
export const googleProvider =
  typeof window === "undefined" || !app ? null : new GoogleAuthProvider();
