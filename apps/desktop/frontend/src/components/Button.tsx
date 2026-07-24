import type { ReactNode } from "react";

type ButtonProps = {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
};

export function Button({ children, className = "secondary", onClick }: ButtonProps) {
  return (
    <button className={`btn ${className}`} type="button" onClick={onClick}>
      {children}
    </button>
  );
}
