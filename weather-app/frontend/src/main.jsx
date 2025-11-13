// bootstrap the React app, without this nothing shows up
// create an entry point to App component
// find the html element with id "root" that is index.html

import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import ErrorBoundary from "./error_handler/ErrorBoundary";
import "./index.css";

const root = createRoot(document.getElementById("root"));
root.render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>
);
