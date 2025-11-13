import React from "react";

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null, info: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    this.setState({ error, info });
    // also log to console for developer
    console.error("ErrorBoundary caught:", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 20 }}>
          <h3 style={{ color: "#b00020" }}>An error occurred</h3>
          <div style={{ whiteSpace: "pre-wrap", fontFamily: "monospace" }}>
            {String(this.state.error && this.state.error.toString())}
            {this.state.info && this.state.info.componentStack
              ? `\n\n${this.state.info.componentStack}`
              : ""}
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
