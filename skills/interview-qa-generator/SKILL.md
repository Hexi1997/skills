---
name: interview-qa-generator
description: This skill should be used when the user asks to "generate interview questions", "generate interview Q&A", "generate interview questions from my resume", "help me prepare for an interview", "mock interview", or wants to generate interview questions and answers based on their resume and project description.
version: 1.0.0
---

# Interview Q&A Generator

This skill automatically generates interview questions and answers based on the user's resume project description and codebase understanding, then saves the output to a file named with the **job title** (see "Save File" below).

## When to Use

When the user needs to:
- Generate interview questions based on resume content
- Simulate interviewer questions
- Prepare for a technical interview
- Generate project-related interview Q&A

## How to Use

### Step 1: Collect Information

Use the AskUserQuestion tool to collect the following from the user:

1. **Project description** (question: "Please provide the project description from your resume")
   - This is the project summary the user wrote in their resume
   - Example: "A Git-driven, local-first blog system where articles are Markdown files and publishing is done via Git Push"

2. **Number of questions** (question: "How many interview questions would you like to generate?", options: 5, 10, 15, 20)
   - Default recommendation is 10 questions
   - Options: 5 / 10 / 15 / 20

3. **Job title** (question: "What is your job title?", options: Frontend Engineer, Senior Frontend Engineer, Full-Stack Engineer, Other)
   - Used to adjust the focus of the questions
   - Used in the output filename; if **Other** is selected, ask for the specific title and use it in the filename

### Step 2: Understand the Codebase

1. Explore the project structure:
   - Read `package.json` to understand the tech stack
   - Read core business logic files
   - Read page components and API implementations

2. Extract key technical points:
   - Architectural design patterns
   - Tech stack and libraries used
   - Core feature implementation approaches
   - Performance optimization strategies
   - Deployment and operations setup

### Step 3: Generate Questions

Generate interview questions across the following dimensions:

1. **Architecture design**: Overall project architecture, reasons for technology choices
2. **Core features**: Implementation principles of main features
3. **Technical depth**: In-depth understanding of key technologies used
4. **Performance optimization**: SSG, static generation, caching strategies
5. **SEO**: Search engine optimization implementation
6. **Data management**: Git-driven content management, version control
7. **Deployment & operations**: Deployment platform selection, CI/CD
8. **Engineering thinking**: Limitations analysis, future roadmap

### Step 4: Generate Answers

Each answer should:
- Reference the actual implementation in the code
- Explain the underlying technical principles
- Provide relevant code snippets
- Describe the business context and rationale for technical decisions

### Step 5: Save File

**Filename** (must include job title):

- Format: `interview-qa-{job-title}.md`
- `{job-title}`: Must match the title collected in Step 1; avoid illegal filename characters such as `/ \ : * ? " < > |`; spaces may be kept or replaced with `-`

Example: `interview-qa-frontend-engineer.md`

Write the generated content to the above path using the following format:

```markdown
# {Project Name} Interview Questions & Answers

> Based on resume description: {user-provided project description}

---

## 1. {Question Title}

**Answer:**
{Detailed answer including code examples}

---
... (more questions)
```

## Output Requirements

- Write questions and answers in English
- Use fenced code blocks with language identifiers for code examples
- Answers should reference specific code locations (e.g., filename:line number)
- Include interview tips and a summary of what each question is testing
- The output file must be named `interview-qa-{job-title}.md`; do not use a fixed name like `interview-qa.md` (job title is required)