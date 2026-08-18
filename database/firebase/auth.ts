import { getApps, initializeApp } from "firebase/app";
import { getAuth } from "firebase/auth";

import { firebaseConfig } from "./config";

const app = getApps()[0] ?? initializeApp(firebaseConfig);
export const auth = getAuth(app);
