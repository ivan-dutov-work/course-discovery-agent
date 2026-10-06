from __future__ import annotations


class MockListing:
    def __init__(
        self,
        keywords: list[str],
        title: str,
        url: str,
        snippet: str,
        duration_hours: float | None = None,
        modules: list[str] | None = None,
    ) -> None:
        self.keywords = keywords
        self.title = title
        self.url = url
        self.snippet = snippet
        self.duration_hours = duration_hours
        self.modules = modules or []


CATALOG: list[MockListing] = [
    MockListing(
        keywords=["python", "beginner", "free", "certificate"],
        title="Python for Everybody Specialization",
        modules=["Programming for Everybody: Getting Started with Python", "Python Data Structures", "Using Python to Access Web Data", "Using Databases with Python", "Capstone: Retrieving, Processing, and Visualizing Data with Python"],
        url="https://www.coursera.org/specializations/python",
        snippet=(
            "University of Michigan specialization on Coursera. Free to audit, "
            "$49/month for the certificate track. Beginner level, English, "
            "4.8 rating from 45,000+ reviews. Includes a shareable certificate "
            "upon paid completion."
        ),
        duration_hours=60,
    ),
    MockListing(
        keywords=["python", "beginner", "free"],
        title="CS50's Introduction to Programming with Python",
        modules=["Lecture 0: Functions, Variables", "Lecture 1: Conditionals", "Lecture 2: Loops", "Lecture 3: Exceptions", "Lecture 4: Libraries", "Lecture 5: Unit Tests", "Lecture 6: File I/O", "Lecture 7: Regular Expressions", "Lecture 8: Object-Oriented Programming", "Lecture 9: Et Cetera"],
        url="https://cs50.harvard.edu/python/",
        snippet=(
            "Harvard's CS50P, free and open, no certificate included in the free "
            "track (verified certificate available for a fee via edX). Beginner "
            "level, English, self-paced."
        ),
        duration_hours=10,
    ),
    MockListing(
        keywords=["python", "intermediate", "certificate"],
        title="100 Days of Code: The Complete Python Pro Bootcamp",
        modules=["Day 1-3: Beginner Python Basics", "Day 4-8: Intermediate Python and Functions", "Day 10-14: Data Structures and Web Scraping", "Day 15-30: Flask Web Apps and Databases", "Day 31-45: Working with APIs and Automation", "Day 46-60: Data Analysis with Pandas", "Day 61-80: Web Development with HTML, CSS and Bootstrap", "Day 81-100: Final Projects"],
        url="https://www.udemy.com/course/100-days-of-code/",
        snippet=(
            "Udemy course, typically discounted to $12-15, rarely fully free. "
            "Includes a certificate of completion. Intermediate level, English, "
            "4.7 rating."
        ),
        duration_hours=60,
    ),
    MockListing(
        keywords=["javascript", "beginner", "free", "certificate"],
        title="freeCodeCamp JavaScript Algorithms and Data Structures",
        modules=["Basic JavaScript", "ES6", "Regular Expressions", "Debugging", "Basic Data Structures", "Basic Algorithm Scripting", "Object-Oriented Programming", "Functional Programming", "Intermediate Algorithm Scripting", "JavaScript Projects"],
        url="https://www.freecodecamp.org/learn/javascript-algorithms-and-data-structures/",
        snippet=(
            "Fully free, includes a verified certificate on completion. "
            "Beginner level, English, self-paced, no rating system on the "
            "platform itself."
        ),
        duration_hours=300,
    ),
    MockListing(
        keywords=["javascript", "web", "intermediate"],
        title="The Complete JavaScript Course 2024",
        modules=["Fundamentals", "How JavaScript Works Behind the Scenes", "Developer Skills and Editor Setup", "Working with Arrays and Loops", "Functions in Depth", "DOM and Events", "Asynchronous JavaScript, AJAX and APIs", "Modern Tooling and Modules", "Final Project"],
        url="https://www.udemy.com/course/the-complete-javascript-course/",
        snippet=(
            "Udemy course, paid ($54.99 list price), includes certificate. "
            "Intermediate level, English, 4.7 rating from 190,000+ students."
        ),
        duration_hours=70,
    ),
    MockListing(
        keywords=["data", "science", "python", "free", "certificate"],
        title="IBM Data Science Professional Certificate",
        modules=["What is Data Science?", "Tools for Data Science", "Data Science Methodology", "Python for Data Science, AI and Development", "Python Project for Data Science", "Databases and SQL for Data Science", "Data Analysis with Python", "Data Visualization with Python", "Machine Learning with Python", "Applied Data Science Capstone"],
        url="https://www.coursera.org/professional-certificates/ibm-data-science",
        snippet=(
            "Coursera professional certificate, free to audit, paid for the "
            "graded certificate ($39/month). Beginner-to-intermediate, English, "
            "4.6 rating."
        ),
        duration_hours=120,
    ),
    MockListing(
        keywords=["machine", "learning", "free", "certificate"],
        title="Machine Learning Specialization (Andrew Ng)",
        modules=["Supervised Machine Learning: Regression and Classification", "Linear Regression and Gradient Descent", "Logistic Regression", "Advanced Learning Algorithms: Neural Networks", "Decision Trees and Tree Ensembles", "Unsupervised Learning and Recommender Systems", "Reinforcement Learning", "Deploying Models to Production"],
        url="https://www.coursera.org/specializations/machine-learning-introduction",
        snippet=(
            "DeepLearning.AI and Stanford Online on Coursera. Free to audit, "
            "certificate requires payment. Intermediate level, English, 4.9 "
            "rating."
        ),
        duration_hours=80,
    ),
    MockListing(
        keywords=["sql", "database", "beginner", "free"],
        title="SQL for Data Analysis",
        modules=["Basic SQL: SELECT and WHERE", "SQL Joins", "SQL Aggregations", "SQL Subqueries and Temporary Tables", "SQL Data Cleaning", "Window Functions", "Advanced SQL Performance Tuning"],
        url="https://www.khanacademy.org/computing/computer-programming/sql",
        snippet=(
            "Khan Academy, entirely free, no certificate offered. Beginner "
            "level, English, self-paced with practice exercises."
        ),
        duration_hours=6,
    ),
    MockListing(
        keywords=["web", "development", "html", "css", "free", "certificate"],
        title="Responsive Web Design Certification",
        modules=["Basic HTML and HTML5", "Basic CSS", "Applied Visual Design", "Applied Accessibility", "Responsive Web Design Principles", "CSS Flexbox", "CSS Grid", "Responsive Web Design Projects"],
        url="https://www.freecodecamp.org/learn/2022/responsive-web-design/",
        snippet=(
            "freeCodeCamp, fully free, includes a verified certificate. "
            "Beginner level, English, project-based curriculum."
        ),
        duration_hours=300,
    ),
    MockListing(
        keywords=["react", "javascript", "intermediate", "certificate"],
        title="React - The Complete Guide",
        modules=["Getting Started", "JavaScript Refresher", "React Essentials: Components, JSX, Props, State", "Rendering Lists and Conditional Content", "Styling React Components", "Working with Refs and Portals", "Side Effects, Hooks and Context", "Redux State Management", "Routing with React Router", "Testing React Apps", "Next.js and Server Components"],
        url="https://www.udemy.com/course/react-the-complete-guide-incl-redux/",
        snippet=(
            "Udemy course, paid ($59.99 list, frequently discounted), includes "
            "certificate. Intermediate level, English, 4.6 rating from 200,000+ "
            "students."
        ),
        duration_hours=68,
    ),
    MockListing(
        keywords=["cloud", "aws", "certificate", "intermediate"],
        title="AWS Certified Cloud Practitioner Essentials",
        modules=["Cloud Concepts", "AWS Core Services: Compute and Storage", "Networking and Content Delivery", "Databases on AWS", "Security and Compliance", "Billing, Pricing and Support", "Exam Preparation and Practice Questions"],
        url="https://aws.amazon.com/training/digital/aws-cloud-practitioner-essentials/",
        snippet=(
            "AWS Skill Builder, free course content; the official certification "
            "exam is a separate paid exam ($100). Beginner-to-intermediate level, "
            "English."
        ),
        duration_hours=6,
    ),
    MockListing(
        keywords=["cybersecurity", "beginner", "free", "certificate"],
        title="Google Cybersecurity Professional Certificate",
        modules=["Foundations of Cybersecurity", "Play It Safe: Manage Security Risks", "Connect and Protect: Networks and Network Security", "Tools of the Trade: Linux and SQL", "Assets, Threats, and Vulnerabilities", "Sound the Alarm: Detection and Response", "Automate Cybersecurity Tasks with Python", "Put It to Work: Prepare for Cybersecurity Jobs"],
        url="https://www.coursera.org/professional-certificates/google-cybersecurity",
        snippet=(
            "Coursera, free to audit, certificate requires a paid subscription "
            "($49/month). Beginner level, English, 4.8 rating."
        ),
        duration_hours=120,
    ),
    MockListing(
        keywords=["python", "advanced", "free"],
        title="Real Python Advanced Python Tutorials",
        modules=["Python Decorators", "Generators and Iterators", "Concurrency with Asyncio", "Metaclasses", "Python Memory Management", "Advanced Topics"],
        url="https://realpython.com/tutorials/advanced/",
        snippet=(
            "Free tutorials, no certificate offered. Advanced level, English, "
            "self-paced articles and videos."
        ),
        duration_hours=8,
    ),
    MockListing(
        keywords=["data", "science", "sql", "free"],
        title="Data Analysis with Python",
        modules=["Numpy", "Pandas", "Data Cleaning with Python", "Data Visualization with Matplotlib and Seaborn", "Statistics for Data Analysis", "Data Analysis Projects"],
        url="https://www.freecodecamp.org/learn/data-analysis-with-python/",
        snippet=(
            "freeCodeCamp, fully free, includes a verified certificate. "
            "Intermediate level, English, project-based."
        ),
        duration_hours=30,
    ),
    MockListing(
        keywords=["javascript", "advanced", "certificate"],
        title="JavaScript: The Advanced Concepts",
        modules=["Introduction", "JavaScript Foundation", "JavaScript Engine and the Call Stack", "Types and Closures", "Object-Oriented Programming", "Functional Programming", "Asynchronous JavaScript", "Modules and Error Handling", "Wrap-up"],
        url="https://www.udemy.com/course/advanced-javascript-concepts/",
        snippet=(
            "Udemy course, paid ($44.99 list price), includes certificate. "
            "Advanced level, English, 4.7 rating."
        ),
        duration_hours=25,
    ),
]
