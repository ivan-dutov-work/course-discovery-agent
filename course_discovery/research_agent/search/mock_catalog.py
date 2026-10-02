from __future__ import annotations


class MockListing:
    def __init__(
        self,
        keywords: list[str],
        title: str,
        url: str,
        snippet: str,
        duration_hours: float | None = None,
    ) -> None:
        self.keywords = keywords
        self.title = title
        self.url = url
        self.snippet = snippet
        self.duration_hours = duration_hours


CATALOG: list[MockListing] = [
    MockListing(
        keywords=["python", "beginner", "free", "certificate"],
        title="Python for Everybody Specialization",
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
        url="https://www.udemy.com/course/advanced-javascript-concepts/",
        snippet=(
            "Udemy course, paid ($44.99 list price), includes certificate. "
            "Advanced level, English, 4.7 rating."
        ),
        duration_hours=25,
    ),
]
